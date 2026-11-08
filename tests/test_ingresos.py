"""Ingresos reales: el tipo `ingreso`, el balance del rango, las frases de ingreso en texto libre y el flujo /ingreso.
Base SQLite en memoria y objetos falsos de Telegram; nada toca la red."""
import asyncio
import datetime as dt
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.handlers import income, natural_language
from app.models import Category, Transaction
from app.services.balance import calcular_balance
from app.services.nlp import parse_expense_text, parse_income_text
from app.services.transactions import ValidationError, create_transaction

HOY = dt.date(2026, 11, 7)


def _mov(session, user, kind, monto, fecha=HOY, **kw):
    if kind == "gasto":
        kw.setdefault("gasto_type", "variable")
    return create_transaction(session, user_id=user.id, kind=kind, description=kw.pop("description", kind),
                              amount=monto, date=fecha, **kw)


# ---- servicio --------------------------------------------------------------------------------------------------------
def test_un_ingreso_no_lleva_categoria_ni_tipo_de_gasto(session, user):
    session.add(Category(user_id=user.id, name="Comida"))
    session.flush()
    with pytest.raises(ValidationError):
        _mov(session, user, "ingreso", 100, category_name="Comida")
    with pytest.raises(ValidationError):
        _mov(session, user, "ingreso", 100, gasto_type="fijo")
    assert _mov(session, user, "ingreso", 100, description="Sueldo").kind == "ingreso"


def test_balance_y_tasa_de_ahorro(session, user):
    _mov(session, user, "ingreso", 4000)
    _mov(session, user, "ingreso", 1000, fecha=HOY - dt.timedelta(days=3))
    _mov(session, user, "gasto", 3000)
    _mov(session, user, "ahorro", 500)
    _mov(session, user, "ingreso", 999, fecha=dt.date(2026, 10, 31))  # fuera del rango
    _mov(session, user, "gasto", 999, status="pendiente")  # pendiente: no cuenta

    b = calcular_balance(session, user.id, dt.date(2026, 11, 1), dt.date(2026, 11, 30))

    assert b.ingresos == Decimal("5000")
    assert b.gastos == Decimal("3000")
    assert b.ahorro_inversion == Decimal("500")
    assert b.balance == Decimal("1500")
    assert b.tasa_ahorro == Decimal("40")


def test_mes_sin_ingresos_no_tiene_tasa_de_ahorro(session, user):
    _mov(session, user, "gasto", 200)
    b = calcular_balance(session, user.id, dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    assert b.balance == Decimal("-200")
    assert b.tasa_ahorro is None


# ---- texto libre -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("frase, monto, concepto", [
    ("me pagaron 4500 de sueldo", 4500, "Sueldo"),
    ("Cobré 1,200 del freelance", 1200, "Freelance"),
    ("me yapearon 50", 50, "Otro"),
    ("recibí 80 de reembolso", 80, "Devolución"),
    ("me depositaron la quincena 2.250,50", 2250.5, "Sueldo"),
])
def test_frases_de_ingreso(frase, monto, concepto):
    p = parse_income_text(frase, today=HOY)
    assert p is not None and p.amount == monto and p.concept == concepto and p.date == HOY


def test_con_varias_cifras_el_monto_es_la_mayor():
    assert parse_income_text("me pagaron el 15 1500", today=HOY).amount == 1500
    assert parse_income_text("me pagaron 1500 el 15", today=HOY).amount == 1500


def test_ingreso_de_ayer():
    assert parse_income_text("ayer me pagaron 300", today=HOY).date == HOY - dt.timedelta(days=1)


@pytest.mark.parametrize("frase", [
    "gasté 50 en almuerzo",
    "recibí la factura de luz y pagué 120",  # verbo de gasto: sigue siendo gasto
    "recibí la factura de luz 120",  # "recibí" sin concepto de ingreso: no es ingreso
    "recibi 50 de vuelto del taxi",
    "me pagaron",  # sin monto
    "taxi 15",
])
def test_frases_que_no_son_ingreso(frase):
    assert parse_income_text(frase, today=HOY) is None


def test_los_gastos_se_siguen_leyendo_igual():
    p = parse_expense_text("gasté 50 en almuerzo", today=HOY)
    assert p.amount == 50 and p.category == "Comida"


# ---- bot -------------------------------------------------------------------------------------------------------------
class FakeUpdate:
    def __init__(self, data=None, text=None):
        self.callback_query = (
            SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock()) if data is not None else None
        )
        self.message = SimpleNamespace(text=text, reply_text=AsyncMock()) if text is not None else None
        self.effective_chat = SimpleNamespace(id=1)
        self.effective_user = SimpleNamespace(first_name="Test")


@pytest.fixture()
def usar_sesion(monkeypatch, session):
    @contextmanager
    def _sesion_de_prueba():
        yield session

    for modulo in (income, natural_language):
        monkeypatch.setattr(modulo, "get_session", _sesion_de_prueba)
    monkeypatch.setattr(income, "hoy", lambda: HOY)


def test_parse_fecha():
    assert income.parse_fecha("15/10", today=HOY) == dt.date(2026, 10, 15)
    assert income.parse_fecha("1-11-2026", today=HOY) == dt.date(2026, 11, 1)
    assert income.parse_fecha("31/12/25", today=HOY) == dt.date(2025, 12, 31)
    assert income.parse_fecha("20/11", today=HOY) is None  # futura
    assert income.parse_fecha("31/02", today=HOY) is None
    assert income.parse_fecha("ayer", today=HOY) is None


def test_flujo_ingreso_completo(session, user, usar_sesion):
    context = SimpleNamespace(user_data={})
    asyncio.run(income.start(FakeUpdate(text="/ingreso"), context))
    asyncio.run(income.on_amount_entered(FakeUpdate(text="1,500"), context))
    asyncio.run(income.on_concept_typed(FakeUpdate(text="Venta de bici"), context))
    asyncio.run(income.on_date_chosen(FakeUpdate(data="ing_date:1"), context))
    confirmar = FakeUpdate(data="ing_confirm")
    asyncio.run(income.on_confirm(confirmar, context))

    tx = session.query(Transaction).one()
    assert (tx.kind, tx.amount, tx.description, tx.date) == ("ingreso", Decimal("1500"), "Venta de bici", HOY - dt.timedelta(days=1))
    assert "Ingresos del mes: S/ 1,500" in confirmar.callback_query.edit_message_text.call_args.args[0]
    assert context.user_data == {}


def test_ingreso_atras_desde_la_fecha_vuelve_al_concepto(session, user, usar_sesion):
    d = {"user_id": user.id, "pasos": ["monto", "concepto", "fecha"], "amount": 100.0, "concept": "Otro"}
    estado = asyncio.run(income.on_back(FakeUpdate(data="ing_back"), SimpleNamespace(user_data=d)))
    assert estado == income.CHOOSING_CONCEPT
    assert d["pasos"] == ["monto", "concepto"]


def test_texto_libre_de_ingreso_pide_confirmacion_y_registra(session, user, usar_sesion):
    context = SimpleNamespace(user_data={})
    mensaje = FakeUpdate(text="me pagaron 4500 de sueldo")
    asyncio.run(natural_language.on_free_text(mensaje, context))
    assert "ingreso de S/ 4,500 (Sueldo)" in mensaje.message.reply_text.call_args.args[0]
    assert session.query(Transaction).count() == 0  # nada sin confirmar

    asyncio.run(income.on_nlu_confirm(FakeUpdate(data="ing_nlu_confirm"), context))
    tx = session.query(Transaction).one()
    assert (tx.kind, tx.amount, tx.description) == ("ingreso", Decimal("4500"), "Sueldo")


def test_texto_libre_cancelado_no_registra(session, user, usar_sesion):
    context = SimpleNamespace(user_data={})
    asyncio.run(natural_language.on_free_text(FakeUpdate(text="cobré 200"), context))
    asyncio.run(income.on_nlu_cancel(FakeUpdate(data="ing_nlu_cancel"), context))
    asyncio.run(income.on_nlu_confirm(FakeUpdate(data="ing_nlu_confirm"), context))  # botón viejo: ya expiró
    assert session.query(Transaction).count() == 0
