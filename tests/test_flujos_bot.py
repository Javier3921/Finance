"""Flujos del bot con la base de datos de prueba y objetos falsos de Telegram: el retroceder de /gasto y de la foto,
la foto que se queda sin usar y los botones que ya no existen. Ningún test toca finanzas.db ni la red."""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.handlers import expense_flow, natural_language, receipt
from app.models import Category, Subcategory


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answer = AsyncMock()
        self.edit_message_text = AsyncMock()


class FakeUpdate:
    def __init__(self, data=None, text=None):
        self.callback_query = FakeQuery(data) if data is not None else None
        self.message = SimpleNamespace(text=text, reply_text=AsyncMock()) if text is not None else None
        self.effective_chat = SimpleNamespace(id=1)
        self.effective_user = SimpleNamespace(first_name="Test")

    def texto_editado(self):
        return self.callback_query.edit_message_text.call_args.args[0]

    def texto_respuesta(self):
        return self.message.reply_text.call_args.args[0]


@pytest.fixture()
def comida(session, user):
    cat = Category(user_id=user.id, name="Comida")
    session.add(cat)
    session.flush()
    session.add(Subcategory(category_id=cat.id, name="Almuerzo"))
    session.flush()
    return cat


@pytest.fixture()
def usar_sesion(monkeypatch, session):
    """Hace que los handlers usen la sesión de prueba en vez de finanzas.db."""
    @contextmanager
    def _sesion_de_prueba():
        yield session

    for modulo in (expense_flow, receipt, natural_language):
        monkeypatch.setattr(modulo, "get_session", _sesion_de_prueba)


# ---- /gasto: retroceder ----------------------------------------------------------------------------------------------
def test_gasto_atras_desde_subcategoria_vuelve_a_las_categorias(session, user, comida, usar_sesion):
    d = {"user_id": user.id, "pasos": ["tipo", "categoria", "subcategoria"], "category_id": comida.id, "category_name": "Comida"}
    update = FakeUpdate(data="gasto_back")

    estado = asyncio.run(expense_flow.on_back(update, SimpleNamespace(user_data=d)))

    assert estado == expense_flow.CHOOSING_CATEGORY
    assert d["pasos"] == ["tipo", "categoria"]
    assert update.texto_editado() == "¿Qué categoría corresponde?"


def test_gasto_tras_volver_elegir_otra_categoria_limpia_la_subcategoria(session, user, comida, usar_sesion):
    otra = Category(user_id=user.id, name="Transporte")
    session.add(otra)
    session.flush()
    d = {"user_id": user.id, "pasos": ["tipo", "categoria", "subcategoria"], "category_id": comida.id,
         "category_name": "Comida", "subcategory_name": "Almuerzo"}
    context = SimpleNamespace(user_data=d)

    asyncio.run(expense_flow.on_back(FakeUpdate(data="gasto_back"), context))
    estado = asyncio.run(expense_flow.on_category_chosen(FakeUpdate(data=f"gasto_cat:{otra.id}"), context))

    assert d["category_name"] == "Transporte"
    assert d["subcategory_name"] is None
    assert estado == expense_flow.ENTERING_AMOUNT  # Transporte no tiene subcategorías: pasa directo al monto


def test_gasto_atras_desde_confirmar_vuelve_al_monto(session, user, comida, usar_sesion):
    d = {"user_id": user.id, "pasos": ["tipo", "categoria", "monto", "confirmar"], "category_id": comida.id,
         "category_name": "Comida", "amount": 35.0, "gasto_type": "variable"}

    estado = asyncio.run(expense_flow.on_back(FakeUpdate(data="gasto_back"), SimpleNamespace(user_data=d)))

    assert estado == expense_flow.ENTERING_AMOUNT
    assert d["pasos"] == ["tipo", "categoria", "monto"]


# ---- foto: retroceder y monto leído ---------------------------------------------------------------------------------
def test_foto_atras_desde_monto_vuelve_a_la_subcategoria(session, user, comida, usar_sesion):
    d = {"user_id": user.id, "pasos": ["categoria", "subcategoria", "monto"], "category_id": comida.id,
         "category_name": "Comida", "subcategory_name": "Almuerzo"}

    estado = asyncio.run(receipt.on_back(FakeUpdate(data="receipt_back"), SimpleNamespace(user_data=d)))

    assert estado == receipt.CHOOSING_SUBCATEGORY
    assert d["pasos"] == ["categoria", "subcategoria"]


def test_foto_usar_el_monto_leido_pasa_a_confirmar(session, user, comida, usar_sesion):
    d = {"user_id": user.id, "pasos": ["categoria", "subcategoria", "monto"], "category_id": comida.id,
         "category_name": "Comida", "subcategory_name": "Almuerzo", "monto_foto": 35.5}

    estado = asyncio.run(receipt.on_ocr_accepted(FakeUpdate(data="receipt_ocr"), SimpleNamespace(user_data=d)))

    assert estado == receipt.CONFIRMING
    assert d["amount"] == 35.5


def test_foto_con_categoria_borrada_avisa_y_se_queda_en_el_paso(session, user, comida, usar_sesion):
    d = {"user_id": user.id, "pasos": ["categoria"]}
    update = FakeUpdate(data="receipt_cat:9999")

    estado = asyncio.run(receipt.on_category_chosen(update, SimpleNamespace(user_data=d)))

    assert estado == receipt.CHOOSING_CATEGORY
    assert "ya no existe" in update.texto_editado()


def test_foto_con_categoria_desactivada_no_avanza(session, user, comida, usar_sesion):
    comida.is_active = False
    session.flush()
    d = {"user_id": user.id, "pasos": ["categoria"]}
    update = FakeUpdate(data=f"receipt_cat:{comida.id}")

    estado = asyncio.run(receipt.on_category_chosen(update, SimpleNamespace(user_data=d)))

    assert estado == receipt.CHOOSING_CATEGORY
    assert "ya no existe" in update.texto_editado()


# ---- texto libre con foto pendiente ----------------------------------------------------------------------------------
def test_pie_de_foto_sin_monto_descarta_la_foto_pendiente(monkeypatch, usar_sesion):
    borrados = []
    monkeypatch.setattr(natural_language.storage, "delete_receipt", borrados.append)
    context = SimpleNamespace(user_data={"nlu_receipt_path": "1/foto.jpg"})
    update = FakeUpdate(text="hola")

    asyncio.run(natural_language.handle_expense_text(update, context, "hola"))

    assert borrados == ["1/foto.jpg"]
    assert "nlu_receipt_path" not in context.user_data
    assert "La foto no se guardó" in update.texto_respuesta()


def test_monto_leido_de_la_foto_se_usa_si_el_pie_no_trae_importe(session, user, comida, usar_sesion):
    context = SimpleNamespace(user_data={"nlu_receipt_path": "1/foto.jpg"})
    update = FakeUpdate(text="almuerzo")

    asyncio.run(natural_language.handle_expense_text(update, context, "almuerzo", monto_foto=42.0))

    assert context.user_data["nlu_amount"] == 42.0
    assert context.user_data["nlu_receipt_path"] == "1/foto.jpg"
    # Sin categoría en el pie: se pregunta cuál es, con el importe ya detectado en la foto.
    assert "en la foto" in update.texto_respuesta()


def test_confirmar_que_falla_borra_la_foto_del_bucket(monkeypatch, session, user, comida, usar_sesion):
    borrados = []
    monkeypatch.setattr(natural_language.storage, "delete_receipt", borrados.append)
    context = SimpleNamespace(user_data={
        "user_id": user.id, "nlu_amount": 20.0, "nlu_date": "2026-10-29", "nlu_category": "Categoría que no existe",
        "nlu_subcategory": None, "nlu_receipt_path": "1/foto.jpg",
    })
    update = FakeUpdate(data="nlu_confirm")

    asyncio.run(natural_language.on_confirm(update, context))

    assert borrados == ["1/foto.jpg"]
    assert "No se pudo registrar" in update.texto_editado()


def test_categoria_inexistente_en_confirmacion_no_revienta(session, user, comida, usar_sesion):
    context = SimpleNamespace(user_data={"user_id": user.id, "nlu_amount": 20.0, "nlu_date": "2026-10-29"})
    update = FakeUpdate(data="nlu_cat:9999")

    asyncio.run(natural_language.on_category_chosen(update, context))

    assert "ya no existe" in update.texto_editado()
    assert "nlu_category" not in context.user_data


def test_subcategoria_desactivada_con_boton_antiguo_no_avanza(session, user, comida, usar_sesion):
    sub = session.query(Subcategory).filter_by(category_id=comida.id).one()
    sub.is_active = False
    session.flush()
    d = {"user_id": user.id, "pasos": ["categoria", "subcategoria"], "category_id": comida.id, "category_name": "Comida"}
    update = FakeUpdate(data=f"receipt_sub:{sub.id}")

    estado = asyncio.run(receipt.on_subcategory_chosen(update, SimpleNamespace(user_data=d)))

    assert estado == receipt.CHOOSING_SUBCATEGORY
    assert "ya no existe" in update.texto_editado()
    assert "subcategory_name" not in d


def test_categoria_de_otro_usuario_en_texto_libre_se_rechaza(session, user, usar_sesion):
    from app.models import User
    otro = User(name="Otro", currency="PEN")
    session.add(otro)
    session.flush()
    ajena = Category(user_id=otro.id, name="Ajena")
    session.add(ajena)
    session.flush()
    context = SimpleNamespace(user_data={"user_id": user.id, "nlu_amount": 20.0, "nlu_date": "2026-10-29"})
    update = FakeUpdate(data=f"nlu_cat:{ajena.id}")

    asyncio.run(natural_language.on_category_chosen(update, context))

    assert "nlu_category" not in context.user_data
    assert "ya no existe" in update.texto_editado()


def test_fecha_de_gasto_usa_la_hora_de_lima(monkeypatch, session, user, comida, usar_sesion):
    """Un gasto confirmado a las 22:00 de Lima (03:00 UTC del día siguiente) queda con la fecha de Lima."""
    import datetime as dt
    from app.models import Transaction
    from app.services.tiempo import hoy as hoy_real

    dia_lima = hoy_real(dt.datetime(2026, 10, 30, 3, 0, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(expense_flow, "hoy", lambda: dia_lima)
    d = {"user_id": user.id, "gasto_type": "variable", "category_id": comida.id, "category_name": "Comida",
         "subcategory_name": None, "amount": 25.0}

    asyncio.run(expense_flow.on_confirm(FakeUpdate(data="gasto_confirm"), SimpleNamespace(user_data=d)))

    guardado = session.query(Transaction).filter_by(user_id=user.id).one()
    assert guardado.date == dt.date(2026, 10, 29)
