"""Ajustes editables por el usuario (ingreso mensual, umbrales de
presupuesto) guardados como datos, no como constantes en el código.

Estos valores se editan desde el panel de configuración
(`app/webapp/config_app.py`) o el bot — nunca hace falta tocar un archivo
de código ni redeployar nada para cambiarlos.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting

# claves conocidas + valor por defecto si el usuario nunca lo cambió
DEFAULTS: dict[str, str] = {
    "monthly_income": "4500",
    "budget_aviso_pct": "70",
    "budget_alerta_pct": "85",
    "budget_excedido_pct": "100",
}


def get_setting(session: Session, user_id: int, key: str) -> str:
    row = session.scalar(select(Setting).where(Setting.user_id == user_id, Setting.key == key))
    if row is not None:
        return row.value
    if key in DEFAULTS:
        return DEFAULTS[key]
    raise KeyError(f"Ajuste desconocido: {key!r}")


def get_setting_float(session: Session, user_id: int, key: str) -> float:
    return float(get_setting(session, user_id, key))


def set_setting(session: Session, user_id: int, key: str, value: str) -> None:
    row = session.scalar(select(Setting).where(Setting.user_id == user_id, Setting.key == key))
    if row is None:
        session.add(Setting(user_id=user_id, key=key, value=str(value)))
    else:
        row.value = str(value)


def all_settings(session: Session, user_id: int) -> dict[str, str]:
    """Todas las claves conocidas con su valor actual (guardado o por
    defecto), para mostrarlas en el panel de configuración."""
    stored = {row.key: row.value for row in session.scalars(select(Setting).where(Setting.user_id == user_id))}
    return {key: stored.get(key, default) for key, default in DEFAULTS.items()}


def seed_defaults_from_env(session: Session, user_id: int, *, monthly_income: float) -> None:
    """Siembra el ingreso mensual inicial desde .env SOLO la primera vez
    (si el usuario ya lo cambió desde el panel, no lo pisa)."""
    if session.scalar(select(Setting).where(Setting.user_id == user_id, Setting.key == "monthly_income")) is None:
        set_setting(session, user_id, "monthly_income", str(monthly_income))
