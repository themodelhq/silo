from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("/", response_model=list[schemas.TransactionOut])
def list_transactions(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
    category: str | None = Query(default=None),
    type: str | None = Query(default=None),
    search: str | None = Query(default=None),
):
    q = db.query(models.Transaction).filter(models.Transaction.user_id == current_user.id)
    if category:
        q = q.filter(models.Transaction.category == category)
    if type:
        q = q.filter(models.Transaction.type == type)
    if search:
        like = f"%{search}%"
        q = q.filter(models.Transaction.note.ilike(like) | models.Transaction.merchant.ilike(like))
    return q.order_by(models.Transaction.occurred_at.desc()).all()


@router.post("/", response_model=schemas.TransactionOut, status_code=201)
def create_transaction(
    payload: schemas.TransactionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    envelope = None
    if payload.envelope_id:
        envelope = db.query(models.Envelope).filter(
            models.Envelope.id == payload.envelope_id, models.Envelope.user_id == current_user.id
        ).first()
        if not envelope:
            raise HTTPException(status_code=404, detail="Envelope not found.")
        if envelope.locked:
            raise HTTPException(status_code=400, detail=f"Envelope '{envelope.name}' is locked.")

        if payload.type == "expense":
            envelope.balance -= payload.amount
        elif payload.type in ("income", "refund"):
            envelope.balance += payload.amount

    txn = models.Transaction(
        user_id=current_user.id,
        envelope_id=payload.envelope_id,
        type=payload.type,
        amount=payload.amount,
        category=payload.category,
        merchant=payload.merchant,
        note=payload.note,
        occurred_at=payload.occurred_at or __import__("datetime").datetime.utcnow(),
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn
