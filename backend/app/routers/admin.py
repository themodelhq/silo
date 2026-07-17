"""Admin endpoints — user management and platform-wide visibility.

There is no separate admin auth system: an "admin login" is simply POST
/auth/login for an account whose `is_admin` flag is true (see
`auth.get_current_admin`). Promote the first admin either by setting
ADMIN_BOOTSTRAP_EMAILS before that account registers, or by running
`python -m scripts.create_admin <email>` against an existing account
(see backend/scripts/create_admin.py).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/me", response_model=schemas.AdminUserOut)
def admin_whoami(current_admin: models.User = Depends(auth.get_current_admin)):
    return current_admin


@router.get("/users", response_model=list[schemas.AdminUserOut])
def list_users(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_admin: models.User = Depends(auth.get_current_admin),
):
    return (
        db.query(models.User)
        .order_by(models.User.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def _get_user_or_404(db: Session, user_id: str) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


@router.get("/users/{user_id}", response_model=schemas.AdminUserOut)
def get_user(user_id: str, db: Session = Depends(get_db), current_admin: models.User = Depends(auth.get_current_admin)):
    return _get_user_or_404(db, user_id)


@router.patch("/users/{user_id}/deactivate", response_model=schemas.AdminUserOut)
def deactivate_user(user_id: str, db: Session = Depends(get_db), current_admin: models.User = Depends(auth.get_current_admin)):
    user = _get_user_or_404(db, user_id)
    if user.id == current_admin.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/activate", response_model=schemas.AdminUserOut)
def activate_user(user_id: str, db: Session = Depends(get_db), current_admin: models.User = Depends(auth.get_current_admin)):
    user = _get_user_or_404(db, user_id)
    user.is_active = True
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/promote", response_model=schemas.AdminUserOut)
def promote_user(user_id: str, db: Session = Depends(get_db), current_admin: models.User = Depends(auth.get_current_admin)):
    """Grant admin privileges to another user. Only existing admins may do this."""
    user = _get_user_or_404(db, user_id)
    user.is_admin = True
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/demote", response_model=schemas.AdminUserOut)
def demote_user(user_id: str, db: Session = Depends(get_db), current_admin: models.User = Depends(auth.get_current_admin)):
    user = _get_user_or_404(db, user_id)
    if user.id == current_admin.id:
        raise HTTPException(status_code=400, detail="You cannot remove your own admin access.")
    user.is_admin = False
    db.commit()
    db.refresh(user)
    return user


@router.get("/stats", response_model=schemas.AdminStatsOut)
def platform_stats(db: Session = Depends(get_db), current_admin: models.User = Depends(auth.get_current_admin)):
    return schemas.AdminStatsOut(
        total_users=db.query(models.User).count(),
        active_users=db.query(models.User).filter(models.User.is_active == True).count(),  # noqa: E712
        admin_users=db.query(models.User).filter(models.User.is_admin == True).count(),  # noqa: E712
        total_payslips=db.query(models.Payslip).count(),
        total_envelopes=db.query(models.Envelope).count(),
        total_transactions=db.query(models.Transaction).count(),
        total_payment_accounts=db.query(models.PaymentAccount).count(),
        total_payment_events=db.query(models.PaymentEvent).count(),
    )


@router.get("/payment-events", response_model=list[schemas.PaymentEventOut])
def list_payment_events(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_admin: models.User = Depends(auth.get_current_admin),
):
    return (
        db.query(models.PaymentEvent)
        .order_by(models.PaymentEvent.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
