"""Liveness и readiness пробы. Без авторизации."""

import sqlalchemy

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.dependencies.db import get_db

router = APIRouter()


@router.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@router.get("/ready", include_in_schema=False)
def ready(db: Session = Depends(get_db)):
    db.execute(sqlalchemy.text("SELECT 1"))
    return {"status": "ready"}
