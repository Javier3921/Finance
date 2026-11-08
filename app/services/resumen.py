"""Resumen de la semana anterior (lunes a domingo), para el mensaje automático de los lunes y para `/semana`.

Cifras: gasto de la semana frente a la previa, ingresos, top 3 categorías, presupuestos del mes en alerta o excedidos
(el mes del domingo de esa semana, hasta ese domingo), recurrentes que vencen en los próximos 7 días y pendientes sin
confirmar. Solo cuentan movimientos confirmados.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.balance import calcular_balance
from app.services.budgets import category_budget_statuses
from app.services.recurring import list_pendientes, proximos_vencimientos
from app.services.transactions import list_transactions


def semana_anterior(today: dt.date) -> tuple[dt.date, dt.date]:
    """(lunes, domingo) de la semana anterior a la de `today`."""
    lunes_actual = today - dt.timedelta(days=today.weekday())
    return lunes_actual - dt.timedelta(days=7), lunes_actual - dt.timedelta(days=1)


@dataclass
class ResumenSemanal:
    inicio: dt.date
    fin: dt.date
    gastado: Decimal
    gastado_previa: Decimal
    ingresos: Decimal
    n_movimientos: int
    top_categorias: list[tuple[str, Decimal]] = field(default_factory=list)
    presupuestos_en_riesgo: list[tuple[str, str, int]] = field(default_factory=list)  # (categoría, estado, % usado)
    proximos: list[tuple[dt.date, str, Decimal, str]] = field(default_factory=list)
    n_pendientes: int = 0

    @property
    def variacion_pct(self) -> Decimal | None:
        if self.gastado_previa <= 0:
            return None
        return (self.gastado - self.gastado_previa) / self.gastado_previa * 100


def construir_resumen(session: Session, user_id: int, today: dt.date) -> ResumenSemanal:
    inicio, fin = semana_anterior(today)
    b = calcular_balance(session, user_id, inicio, fin)
    previa = calcular_balance(session, user_id, inicio - dt.timedelta(days=7), inicio - dt.timedelta(days=1))

    por_categoria: dict[str, Decimal] = defaultdict(Decimal)
    for tx in list_transactions(session, user_id, inicio, fin, kind="gasto"):
        por_categoria[tx.category.name if tx.category else "Sin categoría"] += tx.amount
    top = sorted(por_categoria.items(), key=lambda kv: kv[1], reverse=True)[:3]

    riesgo = [
        (s.category_name, s.status, int(s.pct_used))
        for s in category_budget_statuses(session, user_id, fin.replace(day=1), fin)
        if s.status in ("alerta", "excedido")
    ]
    return ResumenSemanal(
        inicio=inicio, fin=fin, gastado=b.gastos, gastado_previa=previa.gastos, ingresos=b.ingresos,
        n_movimientos=len(list_transactions(session, user_id, inicio, fin)),
        top_categorias=top, presupuestos_en_riesgo=riesgo,
        proximos=proximos_vencimientos(session, user_id, today, today + dt.timedelta(days=6)),
        n_pendientes=len(list_pendientes(session, user_id)),
    )


def _soles(monto) -> str:
    return f"S/ {float(monto):,.0f}"


def texto_resumen(r: ResumenSemanal) -> str:
    rango = f"{r.inicio.strftime('%d/%m')} al {r.fin.strftime('%d/%m')}"
    if r.n_movimientos == 0:
        lineas = [f"📊 Semana del {rango}: sin movimientos registrados."]
    else:
        variacion = f" ({r.variacion_pct:+.0f}% vs. la semana previa)" if r.variacion_pct is not None else ""
        lineas = [f"📊 Resumen de la semana del {rango}", "", f"Gastaste {_soles(r.gastado)}{variacion}."]
        if r.ingresos > 0:
            lineas.append(f"Ingresos: {_soles(r.ingresos)}.")
        if r.top_categorias:
            lineas.append("En qué se fue más:")
            lineas += [f"  {i}. {cat}: {_soles(monto)}" for i, (cat, monto) in enumerate(r.top_categorias, 1)]
    if r.presupuestos_en_riesgo:
        lineas += ["", "⚠️ Presupuestos del mes:"]
        lineas += [f"  {cat}: {pct}% ({'excedido' if est == 'excedido' else 'en alerta'})" for cat, est, pct in r.presupuestos_en_riesgo]
    if r.proximos:
        lineas += ["", "📅 Vencen esta semana:"]
        lineas += [
            f"  {f.strftime('%d/%m')} {nombre}: {_soles(monto)}" + (" (ingreso)" if kind == "ingreso" else "")
            for f, nombre, monto, kind in r.proximos
        ]
    if r.n_pendientes:
        lineas += ["", f"Tienes {r.n_pendientes} recurrente(s) sin confirmar: /pendientes"]
    return "\n".join(lineas)
