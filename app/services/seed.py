"""Datos iniciales: un usuario, categorías/subcategorías, métodos de pago y
los presupuestos del mes actual. Todo esto es EDITABLE después desde el bot
o el panel de configuración — este script solo evita arrancar con la base
de datos vacía, coherente con los mismos ejemplos usados en la vista previa
(`preview/dashboard.html`).

Uso:
    python -m app.services.seed
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.config import settings as app_settings
from app.db import get_session, init_db
from app.models import BudgetPeriod, Category, PaymentMethod, Subcategory, User
from app.services.settings import seed_defaults_from_env

DEFAULT_USER_NAME = "Usuario"  # solo si la base está vacía; editable después

# (categoría, [subcategorías], presupuesto mensual de la categoría)
DEFAULT_CATEGORIES: list[tuple[str, list[str], float]] = [
    ("Comida", ["Almuerzo", "Cena", "Desayuno"], 600),
    ("Salidas", ["Cine", "Restaurante", "Bar"], 300),
    ("Compras", ["Tecnología", "Ropa", "Regalos", "Otros"], 500),
    ("Transporte", [], 150),
    ("Entretenimiento", ["Streaming", "Concierto", "Videojuego"], 100),
    ("Suscripciones", [], 100),
    ("Salud", [], 150),
    ("Otros", [], 100),
]

DEFAULT_PAYMENT_METHODS = ["Efectivo", "Tarjeta de crédito", "Tarjeta de débito", "Transferencia", "Yape", "Plin"]


def seed() -> None:
    init_db()
    today = dt.date.today()

    with get_session() as session:
        # Sistema de un solo usuario: se reutiliza el existente (sea cual sea
        # su nombre) para que volver a correr el seed nunca cree un segundo.
        user = session.scalar(select(User).order_by(User.id).limit(1))
        if user is None:
            user = User(name=DEFAULT_USER_NAME, currency="PEN")
            session.add(user)
            session.flush()  # asigna user.id sin cerrar la transacción

        existing_categories = {c.name for c in session.scalars(select(Category).where(Category.user_id == user.id))}
        existing_methods = {m.name for m in session.scalars(select(PaymentMethod).where(PaymentMethod.user_id == user.id))}

        for order, (cat_name, subcats, budget) in enumerate(DEFAULT_CATEGORIES):
            if cat_name in existing_categories:
                continue
            category = Category(user_id=user.id, name=cat_name, sort_order=order)
            session.add(category)
            session.flush()

            for sub_order, sub_name in enumerate(subcats):
                session.add(Subcategory(category_id=category.id, name=sub_name, sort_order=sub_order))

            if budget:
                session.add(
                    BudgetPeriod(category_id=category.id, year=today.year, month=today.month, amount=budget)
                )

        for name in DEFAULT_PAYMENT_METHODS:
            if name not in existing_methods:
                session.add(PaymentMethod(user_id=user.id, name=name))

        session.flush()
        seed_defaults_from_env(session, user.id, monthly_income=app_settings.monthly_income)

        user_name = user.name

    print(f"Listo. Usuario '{user_name}' con categorías, métodos de pago y presupuestos de "
          f"{today.strftime('%B %Y')} sembrados (o ya existentes).")


if __name__ == "__main__":
    seed()
