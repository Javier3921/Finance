"""Balance de un rango: ingresos, gastos, ahorro + inversión, lo que queda y la tasa de ahorro.

Solo cuenta movimientos confirmados (los pendientes y omitidos no, igual que `list_transactions`). Lo usan el
dashboard, `/resumen` y el resumen semanal, para que los tres den siempre la misma cifra.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Transaction

CERO = Decimal("0")


@dataclass(frozen=True)
class Balance:
    ingresos: Decimal
    gastos: Decimal
    ahorro_inversion: Decimal

    @property
    def balance(self) -> Decimal:
        """Lo que queda libre: ingresos − gastos − ahorro − inversión."""
        return self.ingresos - self.gastos - self.ahorro_inversion

    @property
    def tasa_ahorro(self) -> Decimal | None:
        """% del ingreso que no se gastó (ahorro e inversión cuentan como no gastado). None si no hubo ingresos."""
        if self.ingresos <= 0:
            return None
        return (self.ingresos - self.gastos) / self.ingresos * 100


def totales_por_tipo(session: Session, user_id: int, inicio: dt.date, fin: dt.date) -> dict[str, Decimal]:
    filas = session.execute(
        select(Transaction.kind, func.coalesce(func.sum(Transaction.amount), 0))
        .where(
            Transaction.user_id == user_id,
            Transaction.status == "confirmado",
            Transaction.date >= inicio,
            Transaction.date <= fin,
        )
        .group_by(Transaction.kind)
    ).all()
    return {kind: Decimal(str(total)) for kind, total in filas}


def calcular_balance(session: Session, user_id: int, inicio: dt.date, fin: dt.date) -> Balance:
    t = totales_por_tipo(session, user_id, inicio, fin)
    return Balance(
        ingresos=t.get("ingreso", CERO),
        gastos=t.get("gasto", CERO),
        ahorro_inversion=t.get("ahorro", CERO) + t.get("inversion", CERO),
    )
