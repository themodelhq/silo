from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/envelopes", tags=["envelopes"])


def _get_owned(db: Session, envelope_id: str, user_id: str) -> models.Envelope:
    env = db.query(models.Envelope).filter(
        models.Envelope.id == envelope_id, models.Envelope.user_id == user_id
    ).first()
    if not env:
        raise HTTPException(status_code=404, detail="Envelope not found.")
    return env


@router.get("/", response_model=list[schemas.EnvelopeOut])
def list_envelopes(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    return db.query(models.Envelope).filter(models.Envelope.user_id == current_user.id).all()


@router.get("/rules", response_model=list[schemas.EnvelopeRuleOut])
def list_active_rules(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    """The envelope split currently in effect for incoming payments — either
    generated automatically from the user's most recent payslip upload, or
    set manually. See routers/payslips.py and routers/payments.py."""
    return (
        db.query(models.EnvelopeRule)
        .filter(models.EnvelopeRule.user_id == current_user.id, models.EnvelopeRule.active == True)  # noqa: E712
        .order_by(models.EnvelopeRule.priority)
        .all()
    )


@router.post("/", response_model=schemas.EnvelopeOut, status_code=201)
def create_envelope(payload: schemas.EnvelopeCreate, db: Session = Depends(get_db),
                     current_user: models.User = Depends(auth.get_current_user)):
    env = models.Envelope(user_id=current_user.id, name=payload.name, color=payload.color, priority=payload.priority)
    db.add(env)
    db.commit()
    db.refresh(env)
    return env


@router.patch("/{envelope_id}/rename", response_model=schemas.EnvelopeOut)
def rename_envelope(envelope_id: str, payload: schemas.EnvelopeRename, db: Session = Depends(get_db),
                     current_user: models.User = Depends(auth.get_current_user)):
    env = _get_owned(db, envelope_id, current_user.id)
    env.name = payload.new_name
    db.commit()
    db.refresh(env)
    return env


@router.delete("/{envelope_id}", status_code=204)
def delete_envelope(envelope_id: str, db: Session = Depends(get_db),
                     current_user: models.User = Depends(auth.get_current_user)):
    env = _get_owned(db, envelope_id, current_user.id)
    if env.locked:
        raise HTTPException(status_code=400, detail="Unlock this envelope before deleting it.")
    db.delete(env)
    db.commit()


@router.post("/{envelope_id}/archive", response_model=schemas.EnvelopeOut)
def archive_envelope(envelope_id: str, db: Session = Depends(get_db),
                      current_user: models.User = Depends(auth.get_current_user)):
    env = _get_owned(db, envelope_id, current_user.id)
    env.archived = True
    db.commit()
    db.refresh(env)
    return env


@router.post("/{envelope_id}/lock", response_model=schemas.EnvelopeOut)
def lock_envelope(envelope_id: str, db: Session = Depends(get_db),
                   current_user: models.User = Depends(auth.get_current_user)):
    env = _get_owned(db, envelope_id, current_user.id)
    env.locked = True
    db.commit()
    db.refresh(env)
    return env


@router.post("/{envelope_id}/unlock", response_model=schemas.EnvelopeOut)
def unlock_envelope(envelope_id: str, db: Session = Depends(get_db),
                     current_user: models.User = Depends(auth.get_current_user)):
    env = _get_owned(db, envelope_id, current_user.id)
    env.locked = False
    db.commit()
    db.refresh(env)
    return env


@router.post("/transfer", response_model=list[schemas.EnvelopeOut])
def transfer(payload: schemas.EnvelopeTransfer, db: Session = Depends(get_db),
             current_user: models.User = Depends(auth.get_current_user)):
    source = _get_owned(db, payload.from_envelope_id, current_user.id)
    target = _get_owned(db, payload.to_envelope_id, current_user.id)
    if source.locked:
        raise HTTPException(status_code=400, detail=f"Envelope '{source.name}' is locked.")
    if source.balance < payload.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance in source envelope.")
    source.balance -= payload.amount
    target.balance += payload.amount
    db.commit()
    db.refresh(source)
    db.refresh(target)
    return [source, target]


@router.post("/merge", response_model=schemas.EnvelopeOut)
def merge(payload: schemas.EnvelopeMerge, db: Session = Depends(get_db),
          current_user: models.User = Depends(auth.get_current_user)):
    source = _get_owned(db, payload.source_envelope_id, current_user.id)
    target = _get_owned(db, payload.target_envelope_id, current_user.id)
    target.balance += source.balance
    target.allocated += source.allocated
    db.delete(source)
    db.commit()
    db.refresh(target)
    return target


@router.post("/split", response_model=list[schemas.EnvelopeOut])
def split(payload: schemas.EnvelopeSplit, db: Session = Depends(get_db),
          current_user: models.User = Depends(auth.get_current_user)):
    source = _get_owned(db, payload.source_envelope_id, current_user.id)
    moved_balance = round(source.balance * payload.fraction, 2)
    moved_allocated = round(source.allocated * payload.fraction, 2)
    source.balance -= moved_balance
    source.allocated -= moved_allocated

    new_env = models.Envelope(
        user_id=current_user.id, name=payload.new_name,
        balance=moved_balance, allocated=moved_allocated,
        color=source.color, priority=source.priority,
    )
    db.add(new_env)
    db.commit()
    db.refresh(source)
    db.refresh(new_env)
    return [source, new_env]
