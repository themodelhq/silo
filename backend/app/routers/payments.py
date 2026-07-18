"""Payment API integration.

- POST /payments/accounts   requests a dedicated virtual account number for
  the user (via Paystack's single-step `/dedicated_account/assign`). This is
  asynchronous: the response comes back "pending", and the real account
  number, name, and bank arrive later through the webhook below.
- POST /payments/webhook/paystack   receives Paystack's notifications —
  both the DVA-creation lifecycle events and, once an account is active,
  `charge.success` for actual money received — and splits any incoming
  amount across the user's *active* envelope rules (the split most recently
  generated from an uploaded payslip; see routers/payslips.py). So "upload a
  payslip, then get paid into this account number" ends with money already
  sitting in the right envelopes.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import models, schemas, auth, payments
from ..database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/accounts", response_model=schemas.PaymentAccountOut, status_code=201)
def create_virtual_account(
    payload: schemas.PaymentAccountCreateRequest = schemas.PaymentAccountCreateRequest(),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Request a dedicated virtual account for the current user.

    One live (pending or active) account per user — the envelope split that
    applies to money received into it is always the user's current active
    EnvelopeRule set, so re-uploading a payslip updates the split without
    needing a new account number.

    This call only confirms the *request* was accepted by Paystack; poll
    GET /payments/accounts (or wait for your own notification flow) until
    `status` flips to "active" and `account_number` is populated — see
    schemas.PaymentAccountOut and models.PaymentAccount for the lifecycle.
    """
    existing = (
        db.query(models.PaymentAccount)
        .filter(
            models.PaymentAccount.user_id == current_user.id,
            models.PaymentAccount.active == True,  # noqa: E712
            models.PaymentAccount.status.in_(["pending", "active"]),
        )
        .first()
    )
    if existing:
        return existing

    phone = payload.phone or current_user.phone
    if not phone:
        raise HTTPException(
            status_code=400,
            detail="A phone number is required to create a dedicated virtual account. "
                   "Pass one in this request or set it on your profile first (PATCH /auth/me).",
        )
    if payload.phone and not current_user.phone:
        current_user.phone = payload.phone
        db.add(current_user)

    try:
        payments.assign_dedicated_account(
            email=current_user.email,
            full_name=current_user.full_name,
            phone=phone,
            preferred_bank=payload.preferred_bank or payments.DEFAULT_PREFERRED_BANK,
            country=current_user.country or "NG",
            bvn=payload.bvn,
            bank_code=payload.bank_code,
            account_number=payload.account_number,
        )
    except payments.PaymentProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    account = models.PaymentAccount(
        user_id=current_user.id,
        provider="paystack",
        currency=current_user.currency,
        status="pending",
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get("/accounts", response_model=list[schemas.PaymentAccountOut])
def list_virtual_accounts(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    return db.query(models.PaymentAccount).filter(models.PaymentAccount.user_id == current_user.id).all()


def _get_or_create_envelope(db: Session, user_id: str, name: str, color: str = "#6366F1", priority: int = 99) -> models.Envelope:
    env = db.query(models.Envelope).filter(models.Envelope.user_id == user_id, models.Envelope.name == name).first()
    if env:
        return env
    env = models.Envelope(user_id=user_id, name=name, color=color, priority=priority)
    db.add(env)
    db.flush()
    return env


def _apply_incoming_payment(db: Session, user_id: str, amount: float, reference: str) -> dict:
    """Split an inbound payment across the user's active envelope rules
    (generated from their most recent payslip upload), crediting each
    envelope's balance and logging a transaction per envelope. Returns a
    {envelope_name: amount} breakdown for the PaymentEvent audit record."""
    rules = (
        db.query(models.EnvelopeRule)
        .filter(models.EnvelopeRule.user_id == user_id, models.EnvelopeRule.active == True)  # noqa: E712
        .order_by(models.EnvelopeRule.priority)
        .all()
    )

    breakdown: dict = {}

    if not rules:
        # No envelope split configured yet — don't lose the money, park it
        # somewhere visible and explain why via the transaction note.
        env = _get_or_create_envelope(db, user_id, "Unallocated")
        env.balance = round(env.balance + amount, 2)
        env.allocated = round(env.allocated + amount, 2)
        db.add(models.Transaction(
            user_id=user_id, envelope_id=env.id, type="income", amount=amount,
            category=env.name, note=f"Auto-split from payment {reference}: no active envelope split configured yet.",
        ))
        db.commit()
        return {"Unallocated": amount}

    for rule, portion in payments.split_amount_by_rules(rules, amount):
        if portion <= 0:
            continue
        env = _get_or_create_envelope(db, user_id, rule.envelope_name, rule.color, rule.priority)
        env.balance = round(env.balance + portion, 2)
        env.allocated = round(env.allocated + portion, 2)
        db.add(models.Transaction(
            user_id=user_id, envelope_id=env.id, type="income", amount=portion,
            category=env.name, note=f"Auto-split from payment {reference}",
        ))
        breakdown[rule.envelope_name] = breakdown.get(rule.envelope_name, 0.0) + portion

    db.commit()
    return breakdown


def _find_pending_account_by_email(db: Session, email: str) -> "models.PaymentAccount | None":
    """DVA-lifecycle webhook events carry the customer's email but not our
    internal PaymentAccount id (we don't have a provider reference to key on
    until the account is actually created), so we correlate on the user's
    email plus "most recent pending account" instead."""
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        return None
    return (
        db.query(models.PaymentAccount)
        .filter(
            models.PaymentAccount.user_id == user.id,
            models.PaymentAccount.status == "pending",
        )
        .order_by(models.PaymentAccount.created_at.desc())
        .first()
    )


def _handle_dva_lifecycle_event(db: Session, event: str, data: dict) -> dict:
    """customeridentification.success/.failed and
    dedicatedaccount.assign.success/.failed — the async follow-ups to
    POST /dedicated_account/assign. Paystack's exact payload shape for the
    assign.success/failed events isn't fully pinned down in their public
    docs at the time this was written, so field lookups below are
    defensive (`.get(...)` with fallbacks) — confirm the exact shape against
    your own webhook logs in the Paystack dashboard once you're live, and
    adjust if needed."""
    email = data.get("email") or (data.get("customer") or {}).get("email")
    if not email:
        logger.warning("Paystack webhook %s: no email in payload, cannot correlate to a user.", event)
        return {"status": "ignored", "reason": "no_email_in_payload"}

    account = _find_pending_account_by_email(db, email)
    if not account:
        logger.warning("Paystack webhook %s: no pending PaymentAccount found for %s", event, email)
        return {"status": "no_matching_account"}

    if event == "customeridentification.failed":
        account.status = "failed"
        account.failure_reason = data.get("reason") or "Customer identity validation failed."
        db.commit()
        return {"status": "recorded_failure"}

    if event == "customeridentification.success":
        # Informational — the actual account isn't ready until
        # dedicatedaccount.assign.success arrives, so nothing to persist yet.
        return {"status": "acknowledged"}

    if event == "dedicatedaccount.assign.failed":
        account.status = "failed"
        account.failure_reason = data.get("reason") or data.get("message") or "Dedicated account assignment failed."
        db.commit()
        return {"status": "recorded_failure"}

    if event == "dedicatedaccount.assign.success":
        dva = data.get("dedicated_account") or data
        bank = dva.get("bank") or {}
        account.account_number = dva.get("account_number")
        account.account_name = dva.get("account_name")
        account.bank_name = bank.get("name")
        account.provider_customer_code = (data.get("customer") or {}).get("customer_code")
        account.status = "active" if account.account_number else "failed"
        if not account.account_number:
            account.failure_reason = "Paystack reported success but did not include an account number."
        db.commit()
        return {"status": "activated" if account.status == "active" else "recorded_failure"}

    return {"status": "ignored"}


@router.post("/webhook/paystack", include_in_schema=False)
async def paystack_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature")

    if not payments.verify_webhook_signature(raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Malformed webhook payload.")

    event = payload.get("event")
    data = payload.get("data", {}) or {}

    if event in (
        "customeridentification.success", "customeridentification.failed",
        "dedicatedaccount.assign.success", "dedicatedaccount.assign.failed",
    ):
        return _handle_dva_lifecycle_event(db, event, data)

    if event != "charge.success":
        return {"status": "ignored"}

    reference = data.get("reference")
    if not reference:
        raise HTTPException(status_code=400, detail="Webhook payload missing a transaction reference.")

    # Idempotency: Paystack (like most providers) can redeliver the same event.
    if db.query(models.PaymentEvent).filter(models.PaymentEvent.provider_reference == reference).first():
        return {"status": "duplicate"}

    amount = round(data.get("amount", 0) / 100, 2)  # kobo -> naira
    authorization = data.get("authorization") or {}
    metadata = data.get("metadata") or {}
    account_number = (
        authorization.get("receiver_bank_account_number")
        or metadata.get("receiver_account_number")
        or data.get("account_number")
    )

    account = None
    if account_number:
        account = (
            db.query(models.PaymentAccount)
            .filter(
                models.PaymentAccount.account_number == account_number,
                models.PaymentAccount.active == True,  # noqa: E712
                models.PaymentAccount.status == "active",
            )
            .first()
        )

    if not account:
        logger.warning("Paystack webhook %s: no matching active PaymentAccount for account_number=%r", reference, account_number)
        db.add(models.PaymentEvent(
            user_id=None, payment_account_id=None, provider="paystack", provider_reference=reference,
            amount=amount, account_number=account_number, status="no_matching_account", split_breakdown={},
        ))
        db.commit()
        return {"status": "no_matching_account"}

    breakdown = _apply_incoming_payment(db, account.user_id, amount, reference)

    db.add(models.PaymentEvent(
        user_id=account.user_id, payment_account_id=account.id, provider="paystack",
        provider_reference=reference, amount=amount, account_number=account_number,
        status="processed", split_breakdown=breakdown,
    ))
    db.commit()

    return {"status": "processed", "split": breakdown}
