import hashlib

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas, auth, parser
from ..database import get_db

router = APIRouter(prefix="/payslips", tags=["payslips"])

# Deterministic color palette cycled across auto-generated envelopes so the
# same payslip always produces the same colors run to run.
_ENVELOPE_COLOR_PALETTE = [
    "#6366F1", "#38BDF8", "#10B981", "#F59E0B", "#EF4444",
    "#8B5CF6", "#EC4899", "#14B8A6", "#F97316", "#84CC16",
]


def _generate_envelope_split_from_payslip(
    db: Session, user: models.User, payslip: models.Payslip, parsed: parser.PayslipData
) -> list:
    """Turn a payslip's line items into an active EnvelopeRule set (percentage
    of net salary) and matching Envelope records with the *same names* used
    on the payslip. Supersedes whatever split was previously active for this
    user, so a fresh upload cleanly replaces the last one — this is the
    "specific envelope split assigned to that user" the incoming-payment
    split (see routers/payments.py) reads from.
    """
    if not parsed.line_items:
        return []

    # A percentage needs a denominator. Prefer net salary (the amount that
    # will actually be received); fall back to the sum of the line items
    # themselves if net salary wasn't determinable.
    base = parsed.net_salary if parsed.net_salary > 0 else sum(li["amount"] for li in parsed.line_items)
    if base <= 0:
        return []

    # Supersede any previously active auto-generated (or manual) split.
    db.query(models.EnvelopeRule).filter(
        models.EnvelopeRule.user_id == user.id, models.EnvelopeRule.active == True  # noqa: E712
    ).update({"active": False})

    new_rules = []
    for i, item in enumerate(parsed.line_items):
        if item["amount"] <= 0:
            continue
        value = round(min(item["amount"] / base, 1.0), 4)
        color = _ENVELOPE_COLOR_PALETTE[i % len(_ENVELOPE_COLOR_PALETTE)]

        rule = models.EnvelopeRule(
            user_id=user.id,
            envelope_name=item["name"],
            allocation_type="PERCENTAGE",
            value=value,
            color=color,
            priority=i + 1,
            active=True,
            source_payslip_id=payslip.id,
        )
        db.add(rule)
        new_rules.append(rule)

        # Make sure the matching envelope exists with the same name right
        # away, so it shows up on the dashboard as soon as the payslip is
        # uploaded — not only once a payment is actually received.
        env = db.query(models.Envelope).filter(
            models.Envelope.user_id == user.id, models.Envelope.name == item["name"]
        ).first()
        if not env:
            db.add(models.Envelope(user_id=user.id, name=item["name"], color=color, priority=i + 1))

    db.commit()
    for rule in new_rules:
        db.refresh(rule)
    return new_rules


@router.post("/parse", response_model=schemas.PayslipParseResponse)
def parse_and_save(
    payload: schemas.PayslipParseRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    country_enum = parser.Country(payload.country) if payload.country else None
    parsed = parser.parse_payslip(payload.raw_text, country=country_enum)

    record = models.Payslip(
        user_id=current_user.id,
        employer_name=parsed.employer,
        payroll_month=parsed.payroll_month,
        payroll_year=parsed.payroll_year,
        basic_salary=parsed.basic_salary,
        housing_allowance=parsed.housing_allowance,
        transport_allowance=parsed.transport_allowance,
        utility_allowance=parsed.utility_allowance,
        medical_allowance=parsed.medical_allowance,
        meal_allowance=parsed.meal_allowance,
        bonus=parsed.bonus,
        commission=parsed.commission,
        tax=parsed.tax,
        pension=parsed.pension,
        nhf=parsed.nhf,
        other_deductions=parsed.other_deductions,
        gross_salary=parsed.gross_salary,
        net_salary=parsed.net_salary,
        currency=parsed.currency,
        country=parsed.country,
        validation_status=parsed.validation_status.value,
        extraction_notes=parsed.extraction_notes,
        line_items=parsed.line_items,
        # Store only a hash of the raw text, never the raw text itself,
        # once it has been parsed — matches the brief's "purge raw document" intent.
        raw_text_hash=hashlib.sha256(payload.raw_text.encode()).hexdigest(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    generated_rules = _generate_envelope_split_from_payslip(db, current_user, record, parsed)

    return schemas.PayslipParseResponse(
        payslip=schemas.PayslipOut.model_validate(record),
        generated_envelope_split=[
            schemas.GeneratedEnvelopeSplitOut(
                envelope_name=r.envelope_name, allocation_type=r.allocation_type,
                value=r.value, color=r.color, priority=r.priority,
            )
            for r in generated_rules
        ],
    )


@router.get("/", response_model=list[schemas.PayslipOut])
def list_payslips(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    return (
        db.query(models.Payslip)
        .filter(models.Payslip.user_id == current_user.id)
        .order_by(models.Payslip.created_at.desc())
        .all()
    )
