"""Formato de mensajes compartido entre handlers del bot."""
from __future__ import annotations

from decimal import Decimal

from app.services.budgets import CategoryBudgetStatus

STATUS_LABEL = {"bien": "En rango", "aviso": "Aviso", "alerta": "Alerta", "excedido": "Excedido"}


def money(amount: float | Decimal) -> str:
    return f"S/ {float(amount):,.0f}"


def budget_line(status: CategoryBudgetStatus) -> str:
    label = STATUS_LABEL[status.status]
    if status.status == "excedido":
        tail = f"— {label}, {money(status.over_budget_by)} sobre presupuesto"
    else:
        tail = f"— {label}, disponible {money(status.available)}"
    pct = int(status.pct_used) if status.budget > 0 else 0
    return f"*{status.category_name}*: {money(status.spent)} de {money(status.budget)} ({pct}%) {tail}"
