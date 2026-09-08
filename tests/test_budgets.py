import datetime as dt
from decimal import Decimal

from app.models import BudgetPeriod, Category, Subcategory
from app.services.budgets import (
    category_budget_statuses,
    prorated_budget_for_range,
    resolve_budget,
    subcategory_budget_statuses,
)
from app.services.transactions import create_transaction


def make_category(session, user, name="Comida"):
    cat = Category(user_id=user.id, name=name)
    session.add(cat)
    session.flush()
    return cat


def test_resolve_budget_carries_forward_until_overridden(session, user):
    cat = make_category(session, user)
    session.add(BudgetPeriod(category_id=cat.id, year=2026, month=1, amount=Decimal("500")))
    session.add(BudgetPeriod(category_id=cat.id, year=2026, month=3, amount=Decimal("700")))
    session.flush()

    assert resolve_budget(session, cat.id, 2026, 1) == Decimal("500")
    assert resolve_budget(session, cat.id, 2026, 2) == Decimal("500")  # sin fila propia: hereda de enero
    assert resolve_budget(session, cat.id, 2026, 3) == Decimal("700")
    assert resolve_budget(session, cat.id, 2026, 12) == Decimal("700")
    assert resolve_budget(session, cat.id, 2025, 12) == Decimal("0")  # antes de que existiera cualquier presupuesto


def test_changing_a_budget_does_not_rewrite_past_months(session, user):
    """Ejemplo del prompt original: enero S/500 gastado S/480, luego se sube
    el presupuesto en febrero a S/700 — el histórico de enero no cambia."""
    cat = make_category(session, user)
    session.add(BudgetPeriod(category_id=cat.id, year=2026, month=1, amount=Decimal("500")))
    session.flush()
    create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Comida de enero",
        amount=480, date=dt.date(2026, 1, 15), category_name=cat.name,
    )
    session.add(BudgetPeriod(category_id=cat.id, year=2026, month=2, amount=Decimal("700")))
    session.flush()

    enero = category_budget_statuses(session, user.id, dt.date(2026, 1, 1), dt.date(2026, 1, 31))[0]
    assert enero.budget == Decimal("500")
    assert enero.spent == Decimal("480")

    febrero = category_budget_statuses(session, user.id, dt.date(2026, 2, 1), dt.date(2026, 2, 28))[0]
    assert febrero.budget == Decimal("700")


def test_prorated_budget_full_month_equals_monthly_amount(session, user):
    cat = make_category(session, user)
    session.add(BudgetPeriod(category_id=cat.id, year=2026, month=9, amount=Decimal("600")))
    session.flush()

    total = prorated_budget_for_range(session, cat.id, dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert total == Decimal("600")


def test_prorated_budget_partial_range(session, user):
    cat = make_category(session, user)
    session.add(BudgetPeriod(category_id=cat.id, year=2026, month=9, amount=Decimal("600")))
    session.flush()

    # setiembre tiene 30 días; los primeros 10 días equivalen a 1/3 del presupuesto
    total = prorated_budget_for_range(session, cat.id, dt.date(2026, 9, 1), dt.date(2026, 9, 10))
    assert total == Decimal("600") * 10 / 30


def test_category_budget_statuses_uses_full_month_budget_mid_month(session, user):
    """'¿Cómo voy este mes?' a mitad de mes debe comparar contra el
    presupuesto COMPLETO del mes, no un prorrateo a la fecha de hoy — si no,
    todo se vería falsamente 'excedido' los primeros días del mes."""
    cat = make_category(session, user)
    session.add(BudgetPeriod(category_id=cat.id, year=2026, month=9, amount=Decimal("600")))
    session.flush()
    create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Almuerzo",
        amount=25, date=dt.date(2026, 9, 5), category_name=cat.name,
    )

    # Rango "mes a la fecha": del 1 al 5 de setiembre (recién empieza el mes).
    status = category_budget_statuses(session, user.id, dt.date(2026, 9, 1), dt.date(2026, 9, 5))[0]
    assert status.budget == Decimal("600")
    assert status.spent == Decimal("25")
    assert status.status == "bien"


def test_subcategory_budget_independent_of_category_budget(session, user):
    """Ejemplo del prompt original: Compras S/1,000 en total, pero Tecnología
    dentro de Compras tiene su propio presupuesto de S/500."""
    compras = make_category(session, user, name="Compras")
    tecnologia = Subcategory(category_id=compras.id, name="Tecnología")
    session.add(tecnologia)
    session.flush()

    session.add(BudgetPeriod(category_id=compras.id, year=2026, month=9, amount=Decimal("1000")))
    session.add(BudgetPeriod(category_id=compras.id, subcategory_id=tecnologia.id, year=2026, month=9, amount=Decimal("500")))
    session.flush()

    create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Celular",
        amount=300, date=dt.date(2026, 9, 5), category_name="Compras", subcategory_name="Tecnología",
    )

    # el presupuesto de la categoría completa no se pisa con el de la subcategoría
    cat_status = category_budget_statuses(session, user.id, dt.date(2026, 9, 1), dt.date(2026, 9, 30))[0]
    assert cat_status.budget == Decimal("1000")
    assert cat_status.spent == Decimal("300")

    sub_status = subcategory_budget_statuses(session, user.id, compras.id, dt.date(2026, 9, 1), dt.date(2026, 9, 30))[0]
    assert sub_status.subcategory_name == "Tecnología"
    assert sub_status.budget == Decimal("500")
    assert sub_status.spent == Decimal("300")
    assert sub_status.status == "bien"  # 300/500 = 60%, por debajo del umbral de aviso (70%)


def test_subcategory_without_own_budget_is_not_listed(session, user):
    compras = make_category(session, user, name="Compras")
    session.add(Subcategory(category_id=compras.id, name="Ropa"))
    session.flush()

    # "Ropa" no tiene presupuesto propio ni movimientos: no debe aparecer en el desglose
    results = subcategory_budget_statuses(session, user.id, compras.id, dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert results == []


def test_status_thresholds(session, user):
    thresholds = [("Aviso", 70, "aviso"), ("Alerta", 87, "alerta"), ("Excedido", 130, "excedido"), ("Bien", 40, "bien")]
    for name, spent_pct, expected_status in thresholds:
        cat = make_category(session, user, name=name)
        session.add(BudgetPeriod(category_id=cat.id, year=2026, month=9, amount=Decimal("100")))
        session.flush()
        create_transaction(
            session, user_id=user.id, kind="gasto", gasto_type="variable", description=name,
            amount=spent_pct, date=dt.date(2026, 9, 5), category_name=name,
        )

    statuses = {s.category_name: s for s in category_budget_statuses(session, user.id, dt.date(2026, 9, 1), dt.date(2026, 9, 30))}
    for name, spent_pct, expected_status in thresholds:
        assert statuses[name].status == expected_status, f"{name}: {statuses[name].pct_used}%"
