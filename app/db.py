"""Motor de base de datos y utilidades de sesión (SQLAlchemy)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _make_engine(database_url: str):
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    elif "pooler.supabase.com" in database_url:
        # El *connection pooler* de Supabase en modo "Transaction" reutiliza
        # la misma conexión física de Postgres para clientes distintos entre
        # una transacción y otra — un prepared statement con nombre fijo
        # (lo que hace psycopg3 por defecto) puede colisionar con el de otro
        # cliente en esa misma conexión física ("prepared statement ...
        # already exists"). `prepare_threshold=None` desactiva los prepared
        # statements del lado del servidor, que es lo que Supabase recomienda
        # para este modo de pooler.
        connect_args = {"prepare_threshold": None}
    else:
        connect_args = {}
    return create_engine(database_url, connect_args=connect_args, future=True)


engine = _make_engine(settings.database_url)
# expire_on_commit=False: los handlers del bot (y los tests) suelen seguir
# leyendo atributos ya cargados de un objeto después de que `get_session()`
# hizo commit y cerró la sesión (por ejemplo, para armar un mensaje de
# Telegram). Sin esto, SQLAlchemy expira esos atributos en el commit y la
# siguiente lectura intentaría recargarlos en una sesión ya cerrada.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)


def init_db() -> None:
    """Crea las tablas si no existen. En producción esto se reemplaza por
    migraciones versionadas (Alembic), pero para el MVP local es suficiente."""
    from app import models  # noqa: F401  (registra los modelos en Base.metadata)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


# Columnas agregadas a un modelo DESPUÉS de que alguien ya corrió `init_db()`
# una vez: `create_all` no altera tablas existentes, así que sin esto la
# base de datos local se quedaría atrás del modelo cada vez que se agrega un
# campo. Es un parche a mano hasta introducir Alembic — solo agrega
# columnas, nunca borra ni renombra nada.
_NEW_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "transactions": [("receipt_path", "VARCHAR(300)")],
}


def _add_missing_columns() -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _NEW_COLUMNS.items():
            if table not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, coltype in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}"))


@contextmanager
def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
