import datetime as dt
from decimal import Decimal

import pytest

from app.models import Category, PaymentMethod, Subcategory
from app.services import config as config_service
from app.services.transactions import ValidationError, create_transaction, list_transactions


def _categoria(session, user, nombre="Comida"):
    cat = Category(user_id=user.id, name=nombre)
    session.add(cat)
    session.flush()
    return cat


def _subcategoria(session, cat, nombre="Almuerzo"):
    sub = Subcategory(category_id=cat.id, name=nombre)
    session.add(sub)
    session.flush()
    return sub


def _gasto(session, user, **extra):
    base = dict(
        user_id=user.id, kind="gasto", gasto_type="variable", description="prueba",
        amount=Decimal("10"), date=dt.date(2026, 10, 15),
    )
    base.update(extra)
    return create_transaction(session, **base)


# ---- nombres ---------------------------------------------------------------------------------------------------------
def test_renombrar_categoria_no_acepta_vacio_ni_duplicado(session, user):
    comida = _categoria(session, user)
    _categoria(session, user, "Transporte")

    with pytest.raises(config_service.ConfigError, match="vacío"):
        config_service.rename_category(session, comida.id, "   ")
    with pytest.raises(config_service.ConfigError, match="Ya existe"):
        config_service.rename_category(session, comida.id, "transporte")
    # Su propio nombre (cambiando solo mayúsculas) sí se permite.
    assert config_service.rename_category(session, comida.id, "COMIDA").name == "COMIDA"


def test_renombrar_subcategoria_no_acepta_vacio_ni_duplicado(session, user):
    cat = _categoria(session, user)
    almuerzo = _subcategoria(session, cat)
    _subcategoria(session, cat, "Cena")

    with pytest.raises(config_service.ConfigError, match="vacío"):
        config_service.rename_subcategory(session, almuerzo.id, "")
    with pytest.raises(config_service.ConfigError, match="Ya existe"):
        config_service.rename_subcategory(session, almuerzo.id, "cena")


# ---- presupuestos ----------------------------------------------------------------------------------------------------
def test_presupuesto_rechaza_mes_fuera_de_rango(session, user):
    cat = _categoria(session, user)
    with pytest.raises(config_service.ConfigError, match="entre 1 y 12"):
        config_service.set_budget(session, cat.id, 2026, 13, 500)
    with pytest.raises(config_service.ConfigError, match="entre 1 y 12"):
        config_service.set_budget(session, cat.id, 2026, 0, 500)


def test_presupuesto_rechaza_subcategoria_de_otra_categoria(session, user):
    comida = _categoria(session, user)
    transporte = _categoria(session, user, "Transporte")
    taxi = _subcategoria(session, transporte, "Taxi")
    with pytest.raises(config_service.ConfigError, match="no pertenece"):
        config_service.set_budget(session, comida.id, 2026, 10, 300, subcategory_id=taxi.id)


# ---- movimientos -----------------------------------------------------------------------------------------------------
def test_metodo_de_pago_inexistente_se_rechaza(session, user):
    session.add(PaymentMethod(user_id=user.id, name="Efectivo"))
    session.flush()
    with pytest.raises(ValidationError, match="método de pago"):
        _gasto(session, user, payment_method_name="Tarjeta fantasma")
    assert _gasto(session, user, payment_method_name="efectivo").payment_method_id is not None


def test_desglose_con_importes_no_positivos_o_sin_nombre_se_rechaza(session, user):
    with pytest.raises(ValidationError, match="mayor a cero"):
        _gasto(session, user, amount=Decimal("100"), items=[("Cena", Decimal("150")), ("Extra", Decimal("-50"))])
    with pytest.raises(ValidationError, match="nombre"):
        _gasto(session, user, amount=Decimal("100"), items=[("  ", Decimal("100"))])


def test_estado_desconocido_se_rechaza(session, user):
    with pytest.raises(ValidationError, match="Estado inválido"):
        _gasto(session, user, status="inventado")


def test_pendientes_y_omitidos_no_entran_en_los_listados_por_defecto(session, user):
    _gasto(session, user, amount=Decimal("100"), status="confirmado")
    _gasto(session, user, amount=Decimal("200"), status="omitido")
    _gasto(session, user, amount=Decimal("300"), status="pendiente")
    inicio, fin = dt.date(2026, 10, 1), dt.date(2026, 10, 31)

    confirmados = list_transactions(session, user.id, inicio, fin)
    todos = list_transactions(session, user.id, inicio, fin, status=None)

    assert [t.amount for t in confirmados] == [Decimal("100")]
    assert len(todos) == 3


def test_importe_que_no_es_numero_es_error_de_validacion(session, user):
    with pytest.raises(ValidationError, match="no es un número válido"):
        _gasto(session, user, amount="abc")
    with pytest.raises(ValidationError, match="no es un número válido"):
        _gasto(session, user, amount=Decimal("100"), items=[("Cena", "xyz"), ("Extra", Decimal("0"))])
