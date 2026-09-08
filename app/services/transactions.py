"""Alta y consulta de movimientos (gasto / ahorro / inversión)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    GASTO_TYPES,
    PaymentMethod,
    Subcategory,
    Transaction,
    TransactionItem,
    TRANSACTION_KINDS,
)


class ValidationError(ValueError):
    """Entrada inválida al registrar un movimiento (monto, categoría, etc.)."""


def find_category(session: Session, user_id: int, name: str) -> Category | None:
    """Busca una categoría del usuario por nombre, sin distinguir mayúsculas."""
    name = name.strip().lower()
    candidates = session.scalars(select(Category).where(Category.user_id == user_id, Category.is_active.is_(True)))
    return next((c for c in candidates if c.name.strip().lower() == name), None)


def find_subcategory(session: Session, category_id: int, name: str) -> Subcategory | None:
    name = name.strip().lower()
    return next(
        (
            s
            for s in session.scalars(
                select(Subcategory).where(Subcategory.category_id == category_id, Subcategory.is_active.is_(True))
            )
            if s.name.strip().lower() == name
        ),
        None,
    )


def find_payment_method(session: Session, user_id: int, name: str) -> PaymentMethod | None:
    name = name.strip().lower()
    return next(
        (
            m
            for m in session.scalars(
                select(PaymentMethod).where(PaymentMethod.user_id == user_id, PaymentMethod.is_active.is_(True))
            )
            if m.name.strip().lower() == name
        ),
        None,
    )


def create_transaction(
    session: Session,
    *,
    user_id: int,
    kind: str,
    description: str,
    amount: float | Decimal,
    date: dt.date,
    category_name: str | None = None,
    subcategory_name: str | None = None,
    payment_method_name: str | None = None,
    gasto_type: str | None = None,
    merchant: str | None = None,
    notes: str | None = None,
    items: list[tuple[str, float | Decimal]] | None = None,
    source: str = "manual",
    status: str = "confirmado",
    receipt_path: str | None = None,
) -> Transaction:
    if kind not in TRANSACTION_KINDS:
        raise ValidationError(f"Tipo de movimiento inválido: {kind!r}")
    if kind == "gasto" and gasto_type not in GASTO_TYPES:
        raise ValidationError("Un gasto debe indicar si es 'fijo' o 'variable'")
    if amount is None or Decimal(str(amount)) <= 0:
        raise ValidationError("El monto debe ser mayor a cero")

    category = find_category(session, user_id, category_name) if category_name else None
    if category_name and category is None:
        raise ValidationError(f"No existe la categoría '{category_name}'")

    subcategory = None
    if subcategory_name and category is not None:
        subcategory = find_subcategory(session, category.id, subcategory_name)
        if subcategory is None:
            raise ValidationError(f"No existe la subcategoría '{subcategory_name}' en '{category.name}'")

    payment_method = find_payment_method(session, user_id, payment_method_name) if payment_method_name else None

    if items:
        items_total = sum(Decimal(str(a)) for _, a in items)
        if items_total != Decimal(str(amount)):
            raise ValidationError(
                f"El desglose ({items_total}) no coincide con el monto total ({amount})"
            )

    transaction = Transaction(
        user_id=user_id,
        kind=kind,
        gasto_type=gasto_type,
        category_id=category.id if category else None,
        subcategory_id=subcategory.id if subcategory else None,
        payment_method_id=payment_method.id if payment_method else None,
        description=description,
        amount=Decimal(str(amount)),
        date=date,
        merchant=merchant,
        notes=notes,
        source=source,
        status=status,
        receipt_path=receipt_path,
    )
    session.add(transaction)
    session.flush()

    for name, item_amount in items or []:
        session.add(TransactionItem(transaction_id=transaction.id, name=name, amount=Decimal(str(item_amount))))

    return transaction


def list_transactions(
    session: Session,
    user_id: int,
    start: dt.date,
    end: dt.date,
    *,
    kind: str | None = None,
    category_id: int | None = None,
    subcategory_id: int | None = None,
    with_receipt: bool | None = None,
) -> list[Transaction]:
    stmt = select(Transaction).where(
        Transaction.user_id == user_id,
        Transaction.date >= start,
        Transaction.date <= end,
    )
    if kind:
        stmt = stmt.where(Transaction.kind == kind)
    if category_id:
        stmt = stmt.where(Transaction.category_id == category_id)
    if subcategory_id:
        stmt = stmt.where(Transaction.subcategory_id == subcategory_id)
    if with_receipt is True:
        stmt = stmt.where(Transaction.receipt_path.isnot(None))
    elif with_receipt is False:
        stmt = stmt.where(Transaction.receipt_path.is_(None))
    stmt = stmt.order_by(Transaction.date.desc(), Transaction.id.desc())
    return list(session.scalars(stmt))
