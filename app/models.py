"""Modelo de datos (SQLAlchemy 2.0, estilo declarativo tipado).

Decisiones (ver plan de arquitectura):
- Modelo híbrido: una tabla `transactions` central + tablas de extensión
  (`transaction_items` para desgloses, `investment_details` para inversiones)
  en vez de una tabla única para todo o una tabla separada por tipo.
- Categorías, subcategorías, métodos de pago y presupuestos son DATOS, no
  constantes en código (`app/services/seed.py` solo crea los valores
  iniciales; el usuario puede editarlos después sin tocar el modelo).
- Presupuestos versionados por mes: `BudgetPeriod` solo guarda una fila
  cuando el presupuesto de una categoría CAMBIA; el valor vigente para un
  mes sin fila propia es el de la fila anterior más reciente ("carry
  forward hasta que se sobreescriba"). Esto preserva el histórico: cambiar
  el presupuesto de este mes nunca reescribe el de meses pasados.
- `user_id` en todas las tablas desde el día uno, aunque hoy solo exista un
  usuario — es la inversión barata que evita rediseñar el esquema si el
  sistema se vuelve multiusuario más adelante.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

MONEY = Numeric(12, 2)

# Valores permitidos (documentados aquí; validados en la capa de servicios,
# no como ENUM de base de datos, para no atarnos al dialecto de SQLite vs.
# Postgres y poder agregar un valor nuevo sin migración).
TRANSACTION_KINDS = ("gasto", "ahorro", "inversion")
GASTO_TYPES = ("fijo", "variable")
TRANSACTION_STATUSES = ("confirmado", "pendiente", "omitido")
TRANSACTION_SOURCES = ("manual", "bot_texto", "bot_foto", "recurrente")
RECURRING_FREQUENCIES = ("mensual",)
RECURRING_STATUSES = ("activo", "pausado")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="PEN")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    categories: Mapped[list["Category"]] = relationship(back_populates="user")
    payment_methods: Mapped[list["PaymentMethod"]] = relationship(back_populates="user")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user")
    recurring_expenses: Mapped[list["RecurringExpense"]] = relationship(back_populates="user")


class Setting(Base):
    """Valores ajustables por el usuario sin tocar código (ingreso mensual,
    umbrales de presupuesto, etc.). Clave-valor genérico a propósito: agregar
    un ajuste nuevo no requiere una migración de esquema."""

    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_setting_user_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(String(60))
    value: Mapped[str] = mapped_column(String(200))


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="categories")
    subcategories: Mapped[list["Subcategory"]] = relationship(back_populates="category")
    budget_periods: Mapped[list["BudgetPeriod"]] = relationship(back_populates="category")


class Subcategory(Base):
    __tablename__ = "subcategories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    category: Mapped[Category] = relationship(back_populates="subcategories")


class PaymentMethod(Base):
    __tablename__ = "payment_methods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="payment_methods")


class BudgetPeriod(Base):
    """Presupuesto vigente de una categoría a partir de (year, month).

    No hay una fila por cada mes: solo se inserta una nueva fila cuando el
    presupuesto cambia. Ver `app/services/budgets.py::resolve_budget`.
    """

    __tablename__ = "budget_periods"
    __table_args__ = (
        UniqueConstraint("category_id", "subcategory_id", "year", "month", name="uq_budget_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    subcategory_id: Mapped[int | None] = mapped_column(ForeignKey("subcategories.id"), nullable=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)  # 1-12: mes desde el que aplica este monto
    amount: Mapped[Decimal] = mapped_column(MONEY)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    category: Mapped[Category] = relationship(back_populates="budget_periods")


class RecurringExpense(Base):
    """Gasto fijo recurrente (ej. gimnasio, suscripciones).

    No genera el movimiento en silencio: `app/services/recurring.py` (fase
    siguiente) crea un borrador pendiente de confirmación cada mes, salvo que
    `auto_confirm` esté activo para ese gasto en particular.
    """

    __tablename__ = "recurring_expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    subcategory_id: Mapped[int | None] = mapped_column(ForeignKey("subcategories.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    payment_method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id"), nullable=True)
    frequency: Mapped[str] = mapped_column(String(20), default="mensual")
    due_day: Mapped[int] = mapped_column(Integer, default=1)  # día del mes en que vence/se paga
    auto_confirm: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="activo")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="recurring_expenses")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))  # gasto | ahorro | inversion
    gasto_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # fijo | variable (solo si kind=gasto)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    subcategory_id: Mapped[int | None] = mapped_column(ForeignKey("subcategories.id"), nullable=True)
    payment_method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id"), nullable=True)
    recurring_expense_id: Mapped[int | None] = mapped_column(ForeignKey("recurring_expenses.id"), nullable=True)

    description: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3), default="PEN")
    date: Mapped[dt.date] = mapped_column(Date)
    merchant: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    receipt_path: Mapped[str | None] = mapped_column(String(300), nullable=True)  # foto del comprobante, si se envió una

    status: Mapped[str] = mapped_column(String(20), default="confirmado")
    source: Mapped[str] = mapped_column(String(20), default="manual")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="transactions")
    category: Mapped[Category | None] = relationship()
    subcategory: Mapped[Subcategory | None] = relationship()
    payment_method: Mapped[PaymentMethod | None] = relationship()
    items: Mapped[list["TransactionItem"]] = relationship(back_populates="transaction", cascade="all, delete-orphan")
    investment_detail: Mapped["InvestmentDetail | None"] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", uselist=False
    )


class TransactionItem(Base):
    """Desglose de una transacción (ej. 'Compra de tecnología' -> Celular + Audífonos)."""

    __tablename__ = "transaction_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"))
    name: Mapped[str] = mapped_column(String(120))
    amount: Mapped[Decimal] = mapped_column(MONEY)

    transaction: Mapped[Transaction] = relationship(back_populates="items")


class InvestmentDetail(Base):
    """Datos propios de una transacción kind='inversion' (extensión 1:1)."""

    __tablename__ = "investment_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"), unique=True)
    platform: Mapped[str | None] = mapped_column(String(120), nullable=True)
    asset: Mapped[str | None] = mapped_column(String(120), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)

    transaction: Mapped[Transaction] = relationship(back_populates="investment_detail")
