"""Gastos e ingresos recurrentes: vencimientos, generación mensual sin duplicados y confirmación.

Reglas:
- `due_day`: 1-31 (si el mes es más corto, su último día: 31 -> 28/29 de febrero) o `FIN_DE_MES` (-1). La quincena es
  el día 15 (`QUINCENA`).
- Cada día (y al arrancar el bot) `generar_vencidos` crea, por cada recurrente activo cuyo vencimiento del mes ya
  llegó, UN movimiento ese mes: pendiente, o confirmado si `auto_confirm`. Mira también el mes anterior por si el bot
  estuvo apagado justo en el cambio de mes.
- Nunca duplica: si ya hay un movimiento de ese recurrente en ese mes, en cualquier estado (también omitido), no hace
  nada. Un recurrente no genera vencimientos anteriores a su fecha de creación.
- Los pendientes y omitidos no cuentan en los totales (regla de `list_transactions` y `balance`).
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import (
    Category,
    PaymentMethod,
    RecurringExpense,
    RecurringIncome,
    Subcategory,
    Transaction,
)
from app.services.config import ConfigError
from app.services.tiempo import hoy

FIN_DE_MES = -1
QUINCENA = 15


# ------------------------------------------------------------------------------------------------------ vencimientos
def fecha_vencimiento(due_day: int, year: int, month: int) -> dt.date:
    ultimo = calendar.monthrange(year, month)[1]
    dia = ultimo if due_day == FIN_DE_MES else min(due_day, ultimo)
    return dt.date(year, month, dia)


def describir_vencimiento(due_day: int) -> str:
    if due_day == FIN_DE_MES:
        return "Fin de mes"
    if due_day == QUINCENA:
        return "Quincena (día 15)"
    return f"Día {due_day}"


def _validar_due_day(due_day: int) -> int:
    if due_day != FIN_DE_MES and not 1 <= int(due_day) <= 31:
        raise ConfigError("El día de vencimiento debe estar entre 1 y 31, o ser fin de mes")
    return int(due_day)


def _validar_monto(amount) -> Decimal:
    try:
        valor = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        raise ConfigError("El monto no es un número válido") from None
    if not valor.is_finite() or valor <= 0:
        raise ConfigError("El monto debe ser mayor a cero")
    return valor.quantize(Decimal("0.01"))


def _validar_nombre(name: str) -> str:
    nombre = (name or "").strip()
    if not nombre:
        raise ConfigError("El recurrente necesita un nombre")
    return nombre[:120]


# ------------------------------------------------------------------------------------------------------------- CRUD
def list_recurring_expenses(session: Session, user_id: int) -> list[RecurringExpense]:
    return list(session.scalars(select(RecurringExpense).where(RecurringExpense.user_id == user_id).order_by(RecurringExpense.name)))


def list_recurring_incomes(session: Session, user_id: int) -> list[RecurringIncome]:
    return list(session.scalars(select(RecurringIncome).where(RecurringIncome.user_id == user_id).order_by(RecurringIncome.name)))


def _validar_destino(session: Session, user_id: int, category_id: int, subcategory_id: int | None,
                     payment_method_id: int | None) -> None:
    category = session.get(Category, category_id)
    if category is None or category.user_id != user_id:
        raise ConfigError("Elige una categoría válida")
    if subcategory_id is not None:
        sub = session.get(Subcategory, subcategory_id)
        if sub is None or sub.category_id != category_id:
            raise ConfigError("La subcategoría no pertenece a esa categoría")
    if payment_method_id is not None:
        method = session.get(PaymentMethod, payment_method_id)
        if method is None or method.user_id != user_id:
            raise ConfigError("Elige un método de pago válido")


def save_recurring_expense(
    session: Session, user_id: int, *, name: str, category_id: int, amount, due_day: int,
    subcategory_id: int | None = None, payment_method_id: int | None = None, auto_confirm: bool = False,
    active: bool = True, recurring_id: int | None = None,
) -> RecurringExpense:
    """Crea (sin `recurring_id`) o edita un gasto fijo. Los cambios valen para los meses que aún no se generaron."""
    # Todo se valida ANTES de tocar la sesión: un objeto a medias en la sesión se guardaría en el commit del panel.
    valores = dict(
        name=_validar_nombre(name), amount=_validar_monto(amount), due_day=_validar_due_day(due_day),
        category_id=category_id, subcategory_id=subcategory_id, payment_method_id=payment_method_id,
        auto_confirm=bool(auto_confirm), status="activo" if active else "pausado",
    )
    _validar_destino(session, user_id, category_id, subcategory_id, payment_method_id)
    if recurring_id is None:
        r = RecurringExpense(user_id=user_id, **valores)
        session.add(r)
    else:
        r = session.get(RecurringExpense, recurring_id)
        if r is None or r.user_id != user_id:
            raise ConfigError("Ese gasto fijo ya no existe")
        for campo, valor in valores.items():
            setattr(r, campo, valor)
    session.flush()
    return r


def save_recurring_income(
    session: Session, user_id: int, *, name: str, amount, due_day: int, auto_confirm: bool = False,
    active: bool = True, recurring_id: int | None = None,
) -> RecurringIncome:
    valores = dict(
        name=_validar_nombre(name), amount=_validar_monto(amount), due_day=_validar_due_day(due_day),
        auto_confirm=bool(auto_confirm), status="activo" if active else "pausado",
    )
    if recurring_id is None:
        r = RecurringIncome(user_id=user_id, **valores)
        session.add(r)
    else:
        r = session.get(RecurringIncome, recurring_id)
        if r is None or r.user_id != user_id:
            raise ConfigError("Ese ingreso fijo ya no existe")
        for campo, valor in valores.items():
            setattr(r, campo, valor)
    session.flush()
    return r


def delete_recurring_expense(session: Session, user_id: int, recurring_id: int) -> None:
    """Borra el gasto fijo; los movimientos ya generados se quedan, solo pierden el vínculo."""
    r = session.get(RecurringExpense, recurring_id)
    if r is None or r.user_id != user_id:
        raise ConfigError("Ese gasto fijo ya no existe")
    session.execute(update(Transaction).where(Transaction.recurring_expense_id == recurring_id).values(recurring_expense_id=None))
    session.delete(r)
    session.flush()


def delete_recurring_income(session: Session, user_id: int, recurring_id: int) -> None:
    r = session.get(RecurringIncome, recurring_id)
    if r is None or r.user_id != user_id:
        raise ConfigError("Ese ingreso fijo ya no existe")
    session.execute(update(Transaction).where(Transaction.recurring_income_id == recurring_id).values(recurring_income_id=None))
    session.delete(r)
    session.flush()


# ------------------------------------------------------------------------------------------------------- generación
def _rango_mes(year: int, month: int) -> tuple[dt.date, dt.date]:
    return dt.date(year, month, 1), fecha_vencimiento(FIN_DE_MES, year, month)


def _meses_a_revisar(today: dt.date) -> list[tuple[int, int]]:
    anterior = today.replace(day=1) - dt.timedelta(days=1)
    return [(anterior.year, anterior.month), (today.year, today.month)]


def _creado_el(r) -> dt.date | None:
    """Día de creación en Lima: `created_at` sale de `now()` de la base, en UTC y sin zona horaria."""
    if r.created_at is None:
        return None
    creado = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=dt.timezone.utc)
    return hoy(creado)


def _ya_generado(session: Session, columna, recurring_id: int, year: int, month: int) -> bool:
    inicio, fin = _rango_mes(year, month)
    return session.scalar(
        select(Transaction.id).where(columna == recurring_id, Transaction.date >= inicio, Transaction.date <= fin).limit(1)
    ) is not None


def generar_vencidos(session: Session, user_id: int, today: dt.date) -> list[Transaction]:
    """Crea los movimientos de los recurrentes activos ya vencidos este mes (o el anterior) que aún no existen.
    Devuelve los creados, para avisar por Telegram."""
    creados: list[Transaction] = []
    for year, month in _meses_a_revisar(today):
        for r in session.scalars(select(RecurringExpense).where(RecurringExpense.user_id == user_id, RecurringExpense.status == "activo")):
            vence = fecha_vencimiento(r.due_day, year, month)
            creado = _creado_el(r)
            if vence > today or (creado and vence < creado) or _ya_generado(session, Transaction.recurring_expense_id, r.id, year, month):
                continue
            tx = Transaction(
                user_id=user_id, kind="gasto", gasto_type="fijo", category_id=r.category_id, subcategory_id=r.subcategory_id,
                payment_method_id=r.payment_method_id, recurring_expense_id=r.id, description=r.name, amount=r.amount,
                date=vence, status="confirmado" if r.auto_confirm else "pendiente", source="recurrente",
            )
            session.add(tx)
            creados.append(tx)
        for r in session.scalars(select(RecurringIncome).where(RecurringIncome.user_id == user_id, RecurringIncome.status == "activo")):
            vence = fecha_vencimiento(r.due_day, year, month)
            creado = _creado_el(r)
            if vence > today or (creado and vence < creado) or _ya_generado(session, Transaction.recurring_income_id, r.id, year, month):
                continue
            tx = Transaction(
                user_id=user_id, kind="ingreso", recurring_income_id=r.id, description=r.name, amount=r.amount,
                date=vence, status="confirmado" if r.auto_confirm else "pendiente", source="recurrente",
            )
            session.add(tx)
            creados.append(tx)
        session.flush()
    return creados


# ----------------------------------------------------------------------------------------------------- confirmación
@dataclass(frozen=True)
class Resultado:
    transaction: Transaction
    accion: str  # "confirmado" | "omitido"


def _pendiente(session: Session, user_id: int, transaction_id: int) -> Transaction:
    tx = session.get(Transaction, transaction_id)
    if tx is None or tx.user_id != user_id:
        raise ConfigError("Ese movimiento ya no existe")
    if tx.status != "pendiente":
        raise ConfigError(f"Ese movimiento ya estaba {tx.status}")
    return tx


def confirmar(session: Session, user_id: int, transaction_id: int, *, amount=None) -> Resultado:
    """Confirma un pendiente; con `amount`, con ese monto real en vez del previsto."""
    tx = _pendiente(session, user_id, transaction_id)
    if amount is not None:
        tx.amount = _validar_monto(amount)
    tx.status = "confirmado"
    session.flush()
    return Resultado(tx, "confirmado")


def omitir(session: Session, user_id: int, transaction_id: int) -> Resultado:
    tx = _pendiente(session, user_id, transaction_id)
    tx.status = "omitido"
    session.flush()
    return Resultado(tx, "omitido")


def list_pendientes(session: Session, user_id: int) -> list[Transaction]:
    return list(session.scalars(
        select(Transaction)
        .where(Transaction.user_id == user_id, Transaction.status == "pendiente", Transaction.source == "recurrente")
        .order_by(Transaction.date, Transaction.id)
    ))


def proximos_vencimientos(session: Session, user_id: int, inicio: dt.date, fin: dt.date) -> list[tuple[dt.date, str, Decimal, str]]:
    """(fecha, nombre, monto, "gasto"|"ingreso") de los recurrentes activos que vencen entre `inicio` y `fin`."""
    meses = sorted({(inicio.year, inicio.month), (fin.year, fin.month)})
    salida = []
    for modelo, kind in ((RecurringExpense, "gasto"), (RecurringIncome, "ingreso")):
        for r in session.scalars(select(modelo).where(modelo.user_id == user_id, modelo.status == "activo")):
            for year, month in meses:
                vence = fecha_vencimiento(r.due_day, year, month)
                if inicio <= vence <= fin:
                    salida.append((vence, r.name, r.amount, kind))
    return sorted(salida)
