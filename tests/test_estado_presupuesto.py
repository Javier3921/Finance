import datetime as dt
from decimal import Decimal

from app.bot.formatting import STATUS_LABEL
from app.models import BudgetPeriod, Category
from app.services.budgets import category_budget_statuses
from app.services.transactions import create_transaction


def _gasto(session, user, categoria, monto, fecha):
    create_transaction(
        session,
        user_id=user.id,
        kind="gasto",
        gasto_type="variable",
        description="prueba",
        amount=monto,
        date=fecha,
        category_name=categoria,
    )
    session.flush()


def test_categoria_con_gasto_y_sin_presupuesto_no_sale_en_rango(session, user):
    session.add(Category(user_id=user.id, name="Salidas"))
    session.flush()
    hoy = dt.date(2026, 10, 15)
    _gasto(session, user, "Salidas", Decimal("120"), hoy)

    estados = {e.category_name: e for e in category_budget_statuses(session, user.id, dt.date(2026, 10, 1), hoy)}

    assert estados["Salidas"].budget == 0
    assert estados["Salidas"].status == "sin_presupuesto"
    assert STATUS_LABEL["sin_presupuesto"] == "Sin presupuesto"


def test_categoria_con_presupuesto_sigue_con_su_porcentaje(session, user):
    cat = Category(user_id=user.id, name="Comida")
    session.add(cat)
    session.flush()
    session.add(BudgetPeriod(category_id=cat.id, year=2026, month=10, amount=Decimal("100")))
    session.flush()
    hoy = dt.date(2026, 10, 15)
    _gasto(session, user, "Comida", Decimal("90"), hoy)

    estado = category_budget_statuses(session, user.id, dt.date(2026, 10, 1), hoy)[0]

    assert estado.status == "alerta"  # 90 % con umbrales 70/85/100
