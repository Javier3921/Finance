"""Fecha de hoy para registrar gastos, en la hora de Lima.

`date.today()` usa la zona horaria del proceso: en Streamlit Cloud es UTC, así que de 19:00 a 24:00 en Lima un gasto
caía en el día siguiente. Perú no usa horario de verano desde 1994, por eso basta un desfase fijo de -5 h, sin depender
de la base de datos de zonas horarias del sistema (que Windows no trae).
"""
from __future__ import annotations

import datetime as dt

LIMA = dt.timezone(dt.timedelta(hours=-5), "America/Lima")


def hoy(ahora: dt.datetime | None = None) -> dt.date:
    """La fecha en Lima de `ahora` (con zona horaria) o del momento actual."""
    referencia = ahora or dt.datetime.now(dt.timezone.utc)
    return referencia.astimezone(LIMA).date()
