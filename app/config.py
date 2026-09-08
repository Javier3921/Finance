"""Configuración de la aplicación, leída de variables de entorno (.env).

Por defecto usa SQLite local para desarrollo sin depender de ninguna cuenta
externa. Para producción, DATABASE_URL puede apuntar a Postgres/Supabase sin
cambiar una sola línea de código de los modelos o servicios (SQLAlchemy).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # python-dotenv es opcional en tests
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    telegram_bot_token: str | None
    default_currency: str
    monthly_income: float  # placeholder hasta que exista registro real de ingresos
    supabase_url: str | None
    supabase_service_role_key: str | None
    supabase_receipts_bucket: str


def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'finanzas.db'}"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        default_currency=os.getenv("DEFAULT_CURRENCY", "PEN"),
        monthly_income=float(os.getenv("MONTHLY_INCOME", "4500")),
        supabase_url=os.getenv("SUPABASE_URL") or None,
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or None,
        supabase_receipts_bucket=os.getenv("SUPABASE_RECEIPTS_BUCKET", "receipts"),
    )


settings = get_settings()
