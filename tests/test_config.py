import pytest

from app.services import config as config_service
from app.services.transactions import create_transaction
import datetime as dt


def test_create_and_rename_category(session, user):
    cat = config_service.create_category(session, user.id, "Mascotas")
    assert cat.is_active is True
    config_service.rename_category(session, cat.id, "Mascotas y animales")
    assert cat.name == "Mascotas y animales"


def test_create_category_duplicate_name_raises(session, user):
    config_service.create_category(session, user.id, "Comida")
    with pytest.raises(config_service.ConfigError):
        config_service.create_category(session, user.id, "comida")  # sin distinguir mayúsculas


def test_deactivating_category_keeps_history(session, user):
    cat = config_service.create_category(session, user.id, "Comida")
    create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Almuerzo",
        amount=20, date=dt.date.today(), category_name="Comida",
    )
    assert config_service.count_transactions_for_category(session, cat.id) == 1

    config_service.set_category_active(session, cat.id, False)
    assert cat.is_active is False
    # no aparece en el listado activo...
    assert cat.id not in [c.id for c in config_service.list_categories(session, user.id)]
    # ...pero el movimiento sigue existiendo y apuntando a la misma categoría
    assert config_service.count_transactions_for_category(session, cat.id) == 1


def test_subcategory_crud_and_move(session, user):
    mascotas = config_service.create_category(session, user.id, "Mascotas")
    salud = config_service.create_category(session, user.id, "Salud")
    vet = config_service.create_subcategory(session, mascotas.id, "Veterinario")

    config_service.move_subcategory(session, vet.id, salud.id)
    assert vet.category_id == salud.id
    assert vet.id in [s.id for s in config_service.list_subcategories(session, salud.id)]
    assert vet.id not in [s.id for s in config_service.list_subcategories(session, mascotas.id)]


def test_payment_method_crud(session, user):
    method = config_service.create_payment_method(session, user.id, "Billetera digital")
    assert method.is_active is True
    config_service.set_payment_method_active(session, method.id, False)
    assert method.id not in [m.id for m in config_service.list_payment_methods(session, user.id)]


def test_set_budget_upserts_same_month(session, user):
    cat = config_service.create_category(session, user.id, "Comida")
    config_service.set_budget(session, cat.id, 2026, 9, 600)
    config_service.set_budget(session, cat.id, 2026, 9, 700)  # mismo mes: actualiza, no duplica

    history = config_service.get_budget_history(session, cat.id)
    assert len(history) == 1
    assert history[0].amount == 700


def test_set_budget_negative_amount_raises(session, user):
    cat = config_service.create_category(session, user.id, "Comida")
    with pytest.raises(config_service.ConfigError):
        config_service.set_budget(session, cat.id, 2026, 9, -100)


def test_delete_category_without_transactions_succeeds(session, user):
    cat = config_service.create_category(session, user.id, "Mascotas")
    sub = config_service.create_subcategory(session, cat.id, "Veterinario")
    config_service.set_budget(session, cat.id, 2026, 9, 100)

    config_service.delete_category(session, cat.id)

    assert session.get(config_service.Category, cat.id) is None
    assert session.get(config_service.Subcategory, sub.id) is None  # cascada


def test_delete_category_with_transactions_raises(session, user):
    cat = config_service.create_category(session, user.id, "Comida")
    create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Almuerzo",
        amount=20, date=dt.date.today(), category_name="Comida",
    )
    with pytest.raises(config_service.ConfigError):
        config_service.delete_category(session, cat.id)
    # sigue existiendo: el rechazo no debe dejar nada a medio borrar
    assert session.get(config_service.Category, cat.id) is not None


def test_delete_subcategory_without_transactions_succeeds(session, user):
    cat = config_service.create_category(session, user.id, "Mascotas")
    sub = config_service.create_subcategory(session, cat.id, "Veterinario")

    config_service.delete_subcategory(session, sub.id)

    assert session.get(config_service.Subcategory, sub.id) is None
    assert session.get(config_service.Category, cat.id) is not None  # la categoría no se toca


def test_delete_subcategory_with_transactions_raises(session, user):
    cat = config_service.create_category(session, user.id, "Comida")
    sub = config_service.create_subcategory(session, cat.id, "Almuerzo")
    create_transaction(
        session, user_id=user.id, kind="gasto", gasto_type="variable", description="Almuerzo",
        amount=20, date=dt.date.today(), category_name="Comida", subcategory_name="Almuerzo",
    )
    with pytest.raises(config_service.ConfigError):
        config_service.delete_subcategory(session, sub.id)
    assert session.get(config_service.Subcategory, sub.id) is not None
