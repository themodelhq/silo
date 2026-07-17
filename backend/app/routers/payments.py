"""Payment API integration.

- POST /payments/accounts   generates a dedicated virtual account number the
  user can share with their employer/payer. Money paid into it lands in the
  user's real bank rail via the provider (Paystack), and this API is notified
  by webhook.
- POST /payments/webhook/paystack   receives that notification and splits the
  amount across the user's *active* envelope rules — the split that was most
  recently generated from an uploaded payslip (see routers/payslips.py) — so
  "upload a payslip, then get paid into this account number" ends with money
  already sitting in the right envelopes.
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
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Generate (or return the existing) dedicated virtual account for the
    current user. One active account per user — the envelope split that
    applies to money received into it is always the user's current active
    EnvelopeRule set, so re-uploading a payslip updates the split without
    needing a new account number."""
    existing = (
        db.query(models.PaymentAccount)
        .filter(models.PaymentAccount.user_id == current_user.id, models.PaymentAccount.active == True)  # noqa: E712
        .first()
    )
    if existing:
        return existing

    try:
        customer_code = payments.create_customer(current_user.email, current_user.full_name)
        dva = payments.create_dedicated_virtual_account(customer_code)
    except payments.PaymentProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if not dva.get("account_number"):
        raise HTTPException(status_code=502, detail="Payment provider did not return an account number.")

    account = models.PaymentAccount(
        user_id=current_user.id,
        provider="paystack",
        provider_customer_code=customer_code,
        provider_account_reference=str(dva.get("reference") or ""),
        account_number=dva["account_number"],
        account_name=dva.get("account_name"),
        bank_name=dva.get("bank_name"),
        currency=current_user.currency,
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
    if event != "charge.success":
        return {"status": "ignored"}

    data = payload.get("data", {}) or {}
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
            .filter(models.PaymentAccount.account_number == account_number, models.PaymentAccount.active == True)  # noqa: E712
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
