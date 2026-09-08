"""Fixtures compartidas: una base SQLite en memoria por test, totalmente
aislada de `finanzas.db` (la base de desarrollo real) y de cualquier
credencial externa."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import models  # noqa: F401  (registra los modelos en Base.metadata)
from app.db import Base


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db: Session = TestSession()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def user(session):
    from app.models import User

    u = User(name="Test User", currency="PEN")
    session.add(u)
    session.flush()
    return u
