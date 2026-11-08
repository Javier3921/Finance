"""/deshacer: borra el último movimiento con su desglose y su foto, solo tras confirmar, y nunca uno de otro usuario.
Base SQLite en memoria; el bucket se sustituye por un falso que anota lo que se le pide borrar."""
import asyncio
import datetime as dt
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.handlers import undo
from app.models import Category, Transaction, TransactionItem, User
from app.services.transactions import ValidationError, create_transaction, delete_transaction, last_transaction

HOY = dt.date(2026, 11, 7)


@pytest.fixture()
def comida(session, user):
    cat = Category(user_id=user.id, name="Comida")
    session.add(cat)
    session.flush()
    return cat


def _gasto(session, user, monto, **kw):
    return create_transaction(session, user_id=user.id, kind="gasto", gasto_type="variable", description="x",
                              amount=monto, date=HOY, category_name="Comida", **kw)


# ---- servicio --------------------------------------------------------------------------------------------------------
def test_ultimo_movimiento_es_el_mas_reciente(session, user, comida):
    _gasto(session, user, 10)
    segundo = _gasto(session, user, 20)
    assert last_transaction(session, user.id).id == segundo.id


def test_borra_el_movimiento_con_su_desglose_y_devuelve_la_foto(session, user, comida):
    tx = _gasto(session, user, 30, items=[("a", 10), ("b", 20)], receipt_path="1/foto.jpg")
    assert delete_transaction(session, user.id, tx.id) == "1/foto.jpg"
    assert session.query(Transaction).count() == 0
    assert session.query(TransactionItem).count() == 0


def test_no_borra_movimientos_de_otro_usuario(session, user, comida):
    tx = _gasto(session, user, 30)
    otro = User(name="Otro")
    session.add(otro)
    session.flush()
    with pytest.raises(ValidationError):
        delete_transaction(session, otro.id, tx.id)
    assert session.query(Transaction).count() == 1


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
def bot(monkeypatch, session, user):
    @contextmanager
    def _sesion_de_prueba():
        yield session

    user.telegram_chat_id = "1"
    session.flush()
    borradas = []
    monkeypatch.setattr(undo, "get_session", _sesion_de_prueba)
    monkeypatch.setattr(undo.storage, "delete_receipt", lambda ruta: borradas.append(ruta) or True)
    return borradas


def _callback_de_si(update):
    teclado = update.message.reply_text.call_args.kwargs["reply_markup"]
    return teclado.inline_keyboard[0][0].callback_data


def test_deshacer_muestra_el_ultimo_y_borra_al_confirmar(session, user, comida, bot):
    _gasto(session, user, 10)
    ultimo = _gasto(session, user, 35, receipt_path="1/foto.jpg")

    pregunta = FakeUpdate(text="/deshacer")
    asyncio.run(undo.deshacer_command(pregunta, SimpleNamespace(user_data={})))
    assert "Gasto de S/ 35 en Comida, 07/11/2026 (con foto)" in pregunta.message.reply_text.call_args.args[0]
    assert session.query(Transaction).count() == 2  # preguntar no borra nada

    si = FakeUpdate(data=_callback_de_si(pregunta))
    asyncio.run(undo.on_undo_yes(si, SimpleNamespace(user_data={})))

    assert [t.amount for t in session.query(Transaction)] == [10]
    assert session.get(Transaction, ultimo.id) is None
    assert bot == ["1/foto.jpg"]
    assert si.callback_query.edit_message_text.call_args.args[0].startswith("Borrado: Gasto de S/ 35")


def test_no_no_borra_nada(session, user, comida, bot):
    _gasto(session, user, 10)
    asyncio.run(undo.on_undo_no(FakeUpdate(data="undo_no"), SimpleNamespace(user_data={})))
    assert session.query(Transaction).count() == 1
    assert bot == []


def test_boton_viejo_de_un_movimiento_ya_borrado(session, user, comida, bot):
    tx = _gasto(session, user, 10)
    delete_transaction(session, user.id, tx.id)
    si = FakeUpdate(data=f"undo_yes:{tx.id}")
    asyncio.run(undo.on_undo_yes(si, SimpleNamespace(user_data={})))
    assert "ya no existe" in si.callback_query.edit_message_text.call_args.args[0]


def test_sin_movimientos(session, user, bot):
    pregunta = FakeUpdate(text="/deshacer")
    asyncio.run(undo.deshacer_command(pregunta, SimpleNamespace(user_data={})))
    assert pregunta.message.reply_text.call_args.args[0] == "No hay movimientos registrados que deshacer."


def test_ingreso_se_describe_con_su_concepto(session, user):
    tx = create_transaction(session, user_id=user.id, kind="ingreso", description="Sueldo", amount=4500, date=HOY)
    assert undo.describir(tx) == "Ingreso de S/ 4,500 (Sueldo), 07/11/2026"
