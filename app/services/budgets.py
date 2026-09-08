"""Cálculo de presupuesto vs. gastado por categoría, con prorrateo para
rangos que no son exactamente un mes calendario (mismo criterio que la
vista previa en `preview/dashboard.html`, aquí implementado como fuente de
verdad real sobre la base de datos)."""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BudgetPeriod, Category, Subcategory, Transaction
from app.services.settings import get_setting_float


@dataclass(frozen=True)
class CategoryBudgetStatus:
    category_id: int
    category_name: str
    spent: Decimal
    budget: Decimal
    pct_used: Decimal
    status: str  # "bien" | "aviso" | "alerta" | "excedido"
    subcategory_id: int | None = None
    subcategory_name: str | None = None

    @property
    def available(self) -> Decimal:
        return max(self.budget - self.spent, Decimal("0"))

    @property
    def over_budget_by(self) -> Decimal:
        return max(self.spent - self.budget, Decimal("0"))


def resolve_budget(session: Session, category_id: int, year: int, month: int, subcategory_id: int | None = None) -> Decimal:
    """Presupuesto vigente de la categoría en (year, month): la fila más
    reciente con (year, month) <= al objetivo. Si no hay ninguna, el
    presupuesto es 0 (categoría sin presupuesto asignado)."""
    target = year * 12 + month
    rows = session.scalars(
        select(BudgetPeriod).where(
            BudgetPeriod.category_id == category_id,
            BudgetPeriod.subcategory_id == subcategory_id,
        )
    )
    candidates = [r for r in rows if r.year * 12 + r.month <= target]
    if not candidates:
        return Decimal("0")
    latest = max(candidates, key=lambda r: r.year * 12 + r.month)
    return latest.amount


def full_budget_for_range(
    session: Session, category_id: int, start: dt.date, end: dt.date, subcategory_id: int | None = None
) -> Decimal:
    """Suma el presupuesto MENSUAL COMPLETO de cada mes que toca el rango,
    sin prorratear. Es lo correcto para "¿cómo voy este mes?": a 5 días de
    empezar setiembre, lo que queda disponible se compara contra el
    presupuesto completo de setiembre, no contra 5/30 de él."""
    total = Decimal("0")
    for year, month in _months_touched(start, end):
        total += resolve_budget(session, category_id, year, month, subcategory_id)
    return total


def prorated_budget_for_range(
    session: Session, category_id: int, start: dt.date, end: dt.date, subcategory_id: int | None = None
) -> Decimal:
    """Suma el presupuesto mensual vigente de cada mes que toca el rango,
    prorrateado por los días del rango dentro de ese mes. Útil para un
    rango explícitamente parcial (ej. "hoy", "esta semana") donde se quiere
    un equivalente proporcional en vez del presupuesto completo del mes —
    ver `full_budget_for_range` para el caso "mes a la fecha"."""
    total = Decimal("0")
    cursor = start
    while cursor <= end:
        days_in_month = calendar.monthrange(cursor.year, cursor.month)[1]
        month_end = dt.date(cursor.year, cursor.month, days_in_month)
        seg_end = min(month_end, end)
        days_in_seg = (seg_end - cursor).days + 1
        monthly_budget = resolve_budget(session, category_id, cursor.year, cursor.month, subcategory_id)
        total += monthly_budget * Decimal(days_in_seg) / Decimal(days_in_month)
        cursor = seg_end + dt.timedelta(days=1)
    return total


def _months_touched(start: dt.date, end: dt.date):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        month += 1
        if month == 13:
            month = 1
            year += 1


def _status_for(pct_used: Decimal, thresholds: tuple[Decimal, Decimal, Decimal]) -> str:
    aviso, alerta, excedido = thresholds
    if pct_used >= excedido:
        return "excedido"
    if pct_used >= alerta:
        return "alerta"
    if pct_used >= aviso:
        return "aviso"
    return "bien"


def amount_spent(
    session: Session, user_id: int, category_id: int, start: dt.date, end: dt.date, subcategory_id: int | None = None
) -> Decimal:
    """Total gastado (kind='gasto') en una categoría (y opcionalmente una
    subcategoría puntual) dentro del rango."""
    stmt = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
        Transaction.user_id == user_id,
        Transaction.kind == "gasto",
        Transaction.category_id == category_id,
        Transaction.date >= start,
        Transaction.date <= end,
    )
    if subcategory_id is not None:
        stmt = stmt.where(Transaction.subcategory_id == subcategory_id)
    return Decimal(str(session.scalar(stmt) or 0))


def _get_thresholds(session: Session, user_id: int) -> tuple[Decimal, Decimal, Decimal]:
    """Umbrales de aviso/alerta/excedido: se leen de `Setting` (editables
    desde el panel de configuración), con 70/85/100 como valor por defecto
    si el usuario nunca los cambió."""
    return (
        Decimal(str(get_setting_float(session, user_id, "budget_aviso_pct"))),
        Decimal(str(get_setting_float(session, user_id, "budget_alerta_pct"))),
        Decimal(str(get_setting_float(session, user_id, "budget_excedido_pct"))),
    )


def category_budget_statuses(session: Session, user_id: int, start: dt.date, end: dt.date) -> list[CategoryBudgetStatus]:
    thresholds = _get_thresholds(session, user_id)
    categories = session.scalars(
        select(Category).where(Category.user_id == user_id, Category.is_active.is_(True)).order_by(Category.sort_order)
    ).all()

    results: list[CategoryBudgetStatus] = []
    for category in categories:
        spent = amount_spent(session, user_id, category.id, start, end)
        budget = full_budget_for_range(session, category.id, start, end)
        if budget == 0 and spent == 0:
            continue  # categoría sin presupuesto ni movimientos: no aporta al resumen

        pct_used = (spent / budget * 100) if budget > 0 else Decimal("0")
        results.append(
            CategoryBudgetStatus(
                category_id=category.id,
                category_name=category.name,
                spent=spent,
                budget=budget,
                pct_used=pct_used,
                status=_status_for(pct_used, thresholds),
            )
        )
    return results


def subcategory_budget_statuses(session: Session, user_id: int, category_id: int, start: dt.date, end: dt.date) -> list[CategoryBudgetStatus]:
    """Igual que `category_budget_statuses`, pero desglosado por
    subcategoría dentro de UNA categoría — solo tiene sentido para las
    subcategorías que efectivamente tienen un presupuesto propio definido
    (si no, ya están cubiertas por el presupuesto de la categoría)."""
    thresholds = _get_thresholds(session, user_id)
    subcategories = session.scalars(
        select(Subcategory).where(Subcategory.category_id == category_id, Subcategory.is_active.is_(True)).order_by(Subcategory.sort_order)
    ).all()

    results: list[CategoryBudgetStatus] = []
    for subcategory in subcategories:
        spent = amount_spent(session, user_id, category_id, start, end, subcategory_id=subcategory.id)
        budget = full_budget_for_range(session, category_id, start, end, subcategory_id=subcategory.id)
        if budget == 0 and spent == 0:
            continue  # sin presupuesto propio ni movimientos: no se desglosa aparte

        pct_used = (spent / budget * 100) if budget > 0 else Decimal("0")
        results.append(
            CategoryBudgetStatus(
                category_id=category_id,
                category_name=subcategory.category.name,
                spent=spent,
                budget=budget,
                pct_used=pct_used,
                status=_status_for(pct_used, thresholds),
                subcategory_id=subcategory.id,
                subcategory_name=subcategory.name,
            )
        )
    return results
