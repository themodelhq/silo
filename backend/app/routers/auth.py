from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import os

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


def _is_bootstrap_admin_email(email: str) -> bool:
    """ADMIN_BOOTSTRAP_EMAILS is an optional comma-separated list of emails
    that should automatically become admins the moment they register — a
    convenience for standing up the very first admin account on a fresh
    deployment. See backend/scripts/create_admin.py for a CLI alternative
    that also works against an existing account."""
    configured = os.getenv("ADMIN_BOOTSTRAP_EMAILS", "")
    allowed = {e.strip().lower() for e in configured.split(",") if e.strip()}
    return email.lower() in allowed


@router.post("/register", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: schemas.UserRegister, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    user = models.User(
        email=payload.email,
        hashed_password=auth.hash_password(payload.password),
        full_name=payload.full_name,
        country=payload.country,
        auth_provider="password",
        is_admin=_is_bootstrap_admin_email(payload.email),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=schemas.TokenResponse)
def login(payload: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not user.hashed_password or not auth.verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    token = auth.create_access_token(subject=user.id)
    return schemas.TokenResponse(access_token=token)


@router.get("/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user
