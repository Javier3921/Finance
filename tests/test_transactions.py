import datetime as dt

import pytest
from sqlalchemy import select

from app.models import Category, Subcategory
from app.services.transactions import ValidationError, create_transaction, list_transactions


@pytest.fixture()
def comida(session, user):
    cat = Category(user_id=user.id, name="Comida")
    session.add(cat)
    session.flush()
    session.add(Subcategory(category_id=cat.id, name="Almuerzo"))
    session.flush()
    return cat


def test_create_transaction_basic(session, user, comida):
    tx = create_transaction(
        session,
        user_id=user.id,
        kind="gasto",
        gasto_type="variable",
        description="Almuerzo",
        amount=35,
        date=dt.date(2026, 9, 5),
        category_name="Comida",
        subcategory_name="Almuerzo",
    )
    assert tx.id is not None
    assert tx.category_id == comida.id
    assert float(tx.amount) == 35.0


def test_unknown_category_raises(session, user):
    with pytest.raises(ValidationError):
        create_transaction(
            session,
            user_id=user.id,
            kind="gasto",
            gasto_type="variable",
            description="X",
            amount=10,
            date=dt.date.today(),
            category_name="No Existe",
        )


def test_non_positive_amount_raises(session, user, comida):
    with pytest.raises(ValidationError):
        create_transaction(
            session,
            user_id=user.id,
            kind="gasto",
            gasto_type="variable",
            description="Almuerzo",
            amount=0,
            date=dt.date.today(),
            category_name="Comida",
        )


def test_items_must_sum_to_total(session, user):
    cat = Category(user_id=user.id, name="Compras")
    session.add(cat)
    session.flush()

    with pytest.raises(ValidationError):
        create_transaction(
            session,
            user_id=user.id,
            kind="gasto",
            gasto_type="variable",
            description="Compra de tecnología",
            amount=1500,
            date=dt.date.today(),
            category_name="Compras",
            items=[("Celular", 1200), ("Audífonos", 200)],  # suma 1400 != 1500
        )

    tx = create_transaction(
        session,
        user_id=user.id,
        kind="gasto",
        gasto_type="variable",
        description="Compra de tecnología",
        amount=1500,
        date=dt.date.today(),
        category_name="Compras",
        items=[("Celular", 1200), ("Audífonos", 300)],
    )
    assert sum(float(i.amount) for i in tx.items) == 1500


def test_create_transaction_with_receipt_path(session, user, comida):
    tx = create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Almuerzo",
        amount=25, date=dt.date(2026, 9, 5), category_name="Comida", subcategory_name="Almuerzo",
        source="bot_foto", receipt_path="data/receipts/1/123_abc.jpg",
    )
    assert tx.receipt_path == "data/receipts/1/123_abc.jpg"
    assert tx.source == "bot_foto"


def test_list_transactions_filters_by_receipt(session, user, comida):
    create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Almuerzo",
        amount=25, date=dt.date(2026, 9, 5), category_name="Comida", receipt_path="data/receipts/1/a.jpg",
    )
    create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Cena",
        amount=20, date=dt.date(2026, 9, 5), category_name="Comida",
    )

    with_receipt = list_transactions(session, user.id, dt.date(2026, 9, 1), dt.date(2026, 9, 30), with_receipt=True)
    assert len(with_receipt) == 1
    assert with_receipt[0].receipt_path == "data/receipts/1/a.jpg"

    without_receipt = list_transactions(session, user.id, dt.date(2026, 9, 1), dt.date(2026, 9, 30), with_receipt=False)
    assert len(without_receipt) == 1
    assert without_receipt[0].description == "Cena"


def test_list_transactions_filters_by_subcategory(session, user, comida):
    create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Almuerzo",
        amount=25, date=dt.date(2026, 9, 5), category_name="Comida", subcategory_name="Almuerzo",
    )
    create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Comida suelta",
        amount=15, date=dt.date(2026, 9, 5), category_name="Comida",
    )
    almuerzo_id = session.scalar(select(Subcategory.id).where(Subcategory.name == "Almuerzo"))

    only_sub = list_transactions(session, user.id, dt.date(2026, 9, 1), dt.date(2026, 9, 30), subcategory_id=almuerzo_id)
    assert len(only_sub) == 1
    assert only_sub[0].description == "Almuerzo"


def test_list_transactions_filters_by_range_and_category(session, user, comida):
    create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Almuerzo",
        amount=25, date=dt.date(2026, 9, 1), category_name="Comida",
    )
    create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Almuerzo",
        amount=30, date=dt.date(2026, 8, 15), category_name="Comida",
    )

    september = list_transactions(session, user.id, dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert len(september) == 1
    assert float(september[0].amount) == 25

    by_category = list_transactions(
        session, user.id, dt.date(2026, 1, 1), dt.date(2026, 12, 31), category_id=comida.id
    )
    assert len(by_category) == 2
