from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://centraldsp:centraldsp@postgres:5432/centraldsp")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,      # auto-reconnect on connection loss
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency — yields DB session, always closes on exit"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
