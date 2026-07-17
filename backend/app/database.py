"""Database engine/session configuration.

Defaults to PostgreSQL via DATABASE_URL env var, but falls back to a local
SQLite file so the project runs out-of-the-box for development/testing
without requiring a Postgres server.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./payenvelope.db")

# Render (and Heroku-style providers) hand out DATABASE_URL with the legacy
# "postgres://" scheme, but SQLAlchemy 2.x's psycopg2 dialect requires
# "postgresql://". Normalize it rather than requiring a manual env-var edit
# after every provisioning.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
