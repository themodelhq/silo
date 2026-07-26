"""
Promote (or create) an admin account for Silo.

Usage
-----
    cd backend
    python -m scripts.create_admin admin@example.com --password "Str0ngPass!" --name "Admin"

If a user with that email already exists, it's promoted to admin (its
password is left untouched unless --password is also passed). Otherwise a
new admin account is created with the given password.

This is the CLI counterpart to the ADMIN_BOOTSTRAP_EMAILS environment
variable (see app/routers/auth.py), which auto-promotes matching emails at
registration time instead.
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import models, auth
from app.database import SessionLocal, engine


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or promote a Silo admin user.")
    parser.add_argument("email", help="Email address of the admin account.")
    parser.add_argument("--password", help="Password to set (required when creating a new account).")
    parser.add_argument("--name", default=None, help="Full name for a newly created account.")
    parser.add_argument("--country", default="NG", help="Country code for a newly created account (default: NG).")
    args = parser.parse_args()

    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.email == args.email).first()

        if user:
            user.is_admin = True
            if args.password:
                user.hashed_password = auth.hash_password(args.password)
            db.commit()
            print(f"'{args.email}' already existed and has been promoted to admin.")
            return

        if not args.password:
            print("No account with that email exists yet — pass --password to create one.", file=sys.stderr)
            sys.exit(1)

        user = models.User(
            email=args.email,
            hashed_password=auth.hash_password(args.password),
            full_name=args.name,
            country=args.country,
            auth_provider="password",
            is_admin=True,
        )
        db.add(user)
        db.commit()
        print(f"Created new admin account for '{args.email}'.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
