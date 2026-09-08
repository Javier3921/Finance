"""CRUD de configuración: categorías, subcategorías, métodos de pago y
presupuestos. Esto es lo que respalda el panel de configuración — nada de
esto vive en el código como constante, todo es una fila editable.

Principio (ver README del proyecto): nunca se borra una categoría o
subcategoría que ya tiene movimientos —  se desactiva. Así el histórico
jamás se pierde ni queda huérfano, y no hace falta un flujo de "reasignar
movimientos antes de borrar"."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BudgetPeriod, Category, PaymentMethod, Subcategory, Transaction


class ConfigError(ValueError):
    """Operación de configuración inválida (nombre duplicado, monto inválido, etc.)."""


def _norm(name: str) -> str:
    return name.strip()


# ---------------------------------------------------------------- categorías
def list_categories(session: Session, user_id: int, *, include_inactive: bool = False) -> list[Category]:
    stmt = select(Category).where(Category.user_id == user_id).order_by(Category.sort_order, Category.name)
    if not include_inactive:
        stmt = stmt.where(Category.is_active.is_(True))
    return list(session.scalars(stmt))


def create_category(session: Session, user_id: int, name: str) -> Category:
    name = _norm(name)
    if not name:
        raise ConfigError("El nombre de la categoría no puede estar vacío")
    exists = any(c.name.lower() == name.lower() for c in list_categories(session, user_id, include_inactive=True))
    if exists:
        raise ConfigError(f"Ya existe una categoría llamada '{name}'")
    max_order = session.scalar(select(func.max(Category.sort_order)).where(Category.user_id == user_id)) or 0
    category = Category(user_id=user_id, name=name, sort_order=max_order + 1)
    session.add(category)
    session.flush()
    return category


def rename_category(session: Session, category_id: int, new_name: str) -> Category:
    category = session.get(Category, category_id)
    if category is None:
        raise ConfigError("Categoría no encontrada")
    category.name = _norm(new_name)
    return category


def set_category_active(session: Session, category_id: int, active: bool) -> Category:
    category = session.get(Category, category_id)
    if category is None:
        raise ConfigError("Categoría no encontrada")
    category.is_active = active
    return category


def count_transactions_for_category(session: Session, category_id: int) -> int:
    return session.scalar(select(func.count(Transaction.id)).where(Transaction.category_id == category_id)) or 0


def delete_category(session: Session, category_id: int) -> None:
    """Borra la categoría de verdad (no solo la desactiva). Solo se permite
    si no tiene NINGÚN movimiento asociado — si tiene, se rechaza con un
    mensaje claro y la alternativa (desactivar) en vez de arriesgar el
    histórico. Sin movimientos, sus subcategorías y presupuestos tampoco
    tienen histórico que perder, así que se borran en cascada."""
    category = session.get(Category, category_id)
    if category is None:
        raise ConfigError("Categoría no encontrada")
    n_tx = count_transactions_for_category(session, category_id)
    if n_tx > 0:
        raise ConfigError(
            f"No se puede eliminar '{category.name}': tiene {n_tx} movimiento(s) registrados. "
            "Desactívala en su lugar para conservar el histórico."
        )
    for sub in session.scalars(select(Subcategory).where(Subcategory.category_id == category_id)):
        session.delete(sub)
    for budget in session.scalars(select(BudgetPeriod).where(BudgetPeriod.category_id == category_id)):
        session.delete(budget)
    session.delete(category)
    session.flush()


# ------------------------------------------------------------- subcategorías
def list_subcategories(session: Session, category_id: int, *, include_inactive: bool = False) -> list[Subcategory]:
    stmt = select(Subcategory).where(Subcategory.category_id == category_id).order_by(Subcategory.sort_order, Subcategory.name)
    if not include_inactive:
        stmt = stmt.where(Subcategory.is_active.is_(True))
    return list(session.scalars(stmt))


def create_subcategory(session: Session, category_id: int, name: str) -> Subcategory:
    name = _norm(name)
    if not name:
        raise ConfigError("El nombre de la subcategoría no puede estar vacío")
    exists = any(s.name.lower() == name.lower() for s in list_subcategories(session, category_id, include_inactive=True))
    if exists:
        raise ConfigError(f"Ya existe una subcategoría llamada '{name}' en esta categoría")
    max_order = session.scalar(select(func.max(Subcategory.sort_order)).where(Subcategory.category_id == category_id)) or 0
    subcategory = Subcategory(category_id=category_id, name=name, sort_order=max_order + 1)
    session.add(subcategory)
    session.flush()
    return subcategory


def rename_subcategory(session: Session, subcategory_id: int, new_name: str) -> Subcategory:
    subcategory = session.get(Subcategory, subcategory_id)
    if subcategory is None:
        raise ConfigError("Subcategoría no encontrada")
    subcategory.name = _norm(new_name)
    return subcategory


def move_subcategory(session: Session, subcategory_id: int, new_category_id: int) -> Subcategory:
    subcategory = session.get(Subcategory, subcategory_id)
    if subcategory is None:
        raise ConfigError("Subcategoría no encontrada")
    if session.get(Category, new_category_id) is None:
        raise ConfigError("La categoría de destino no existe")
    subcategory.category_id = new_category_id
    return subcategory


def set_subcategory_active(session: Session, subcategory_id: int, active: bool) -> Subcategory:
    subcategory = session.get(Subcategory, subcategory_id)
    if subcategory is None:
        raise ConfigError("Subcategoría no encontrada")
    subcategory.is_active = active
    return subcategory


def count_transactions_for_subcategory(session: Session, subcategory_id: int) -> int:
    return session.scalar(select(func.count(Transaction.id)).where(Transaction.subcategory_id == subcategory_id)) or 0


def delete_subcategory(session: Session, subcategory_id: int) -> None:
    subcategory = session.get(Subcategory, subcategory_id)
    if subcategory is None:
        raise ConfigError("Subcategoría no encontrada")
    n_tx = count_transactions_for_subcategory(session, subcategory_id)
    if n_tx > 0:
        raise ConfigError(
            f"No se puede eliminar '{subcategory.name}': tiene {n_tx} movimiento(s) registrados. "
            "Desactívala en su lugar para conservar el histórico."
        )
    for budget in session.scalars(select(BudgetPeriod).where(BudgetPeriod.subcategory_id == subcategory_id)):
        session.delete(budget)
    session.delete(subcategory)
    session.flush()


# ------------------------------------------------------------ métodos de pago
def list_payment_methods(session: Session, user_id: int, *, include_inactive: bool = False) -> list[PaymentMethod]:
    stmt = select(PaymentMethod).where(PaymentMethod.user_id == user_id).order_by(PaymentMethod.name)
    if not include_inactive:
        stmt = stmt.where(PaymentMethod.is_active.is_(True))
    return list(session.scalars(stmt))


def create_payment_method(session: Session, user_id: int, name: str) -> PaymentMethod:
    name = _norm(name)
    if not name:
        raise ConfigError("El nombre del método de pago no puede estar vacío")
    exists = any(m.name.lower() == name.lower() for m in list_payment_methods(session, user_id, include_inactive=True))
    if exists:
        raise ConfigError(f"Ya existe un método de pago llamado '{name}'")
    method = PaymentMethod(user_id=user_id, name=name)
    session.add(method)
    session.flush()
    return method


def set_payment_method_active(session: Session, method_id: int, active: bool) -> PaymentMethod:
    method = session.get(PaymentMethod, method_id)
    if method is None:
        raise ConfigError("Método de pago no encontrado")
    method.is_active = active
    return method


# -------------------------------------------------------------- presupuestos
def set_budget(
    session: Session, category_id: int, year: int, month: int, amount: float | Decimal, *, subcategory_id: int | None = None
) -> BudgetPeriod:
    """Fija el presupuesto de la categoría a partir de (year, month) en
    adelante (hasta que se vuelva a cambiar) — es un upsert sobre la fila de
    ese mes exacto; los meses anteriores nunca se tocan."""
    amount = Decimal(str(amount))
    if amount < 0:
        raise ConfigError("El presupuesto no puede ser negativo")
    row = session.scalar(
        select(BudgetPeriod).where(
            BudgetPeriod.category_id == category_id,
            BudgetPeriod.subcategory_id == subcategory_id,
            BudgetPeriod.year == year,
            BudgetPeriod.month == month,
        )
    )
    if row is None:
        row = BudgetPeriod(category_id=category_id, subcategory_id=subcategory_id, year=year, month=month, amount=amount)
        session.add(row)
    else:
        row.amount = amount
    session.flush()
    return row


def get_budget_history(session: Session, category_id: int, *, subcategory_id: int | None = None) -> list[BudgetPeriod]:
    stmt = (
        select(BudgetPeriod)
        .where(BudgetPeriod.category_id == category_id, BudgetPeriod.subcategory_id == subcategory_id)
        .order_by(BudgetPeriod.year.desc(), BudgetPeriod.month.desc())
    )
    return list(session.scalars(stmt))
