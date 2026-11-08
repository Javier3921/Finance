"""Recurrentes: cálculo de vencimientos, generación sin duplicados, confirmar / cambiar monto / omitir, auto_confirm,
el aviso diario del bot y el CRUD del panel. Base SQLite en memoria; Telegram simulado."""
import asyncio
import datetime as dt
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot import jobs
from app.bot.handlers import recurring as handlers
from app.models import Category, RecurringExpense, RecurringIncome, Transaction
from app.services import recurring as rs
from app.services.balance import calcular_balance
from app.services.config import ConfigError

HOY = dt.date(2026, 11, 7)
CREADO = dt.datetime(2026, 11, 1, 12, 0)  # UTC, como lo guarda la base


@pytest.fixture()
def cat(session, user):
    c = Category(user_id=user.id, name="Entretenimiento")
    session.add(c)
    session.flush()
    return c


def _gasto_fijo(session, user, cat, due_day, monto=45, **kw):
    creado = kw.pop("created_at", CREADO)
    r = rs.save_recurring_expense(session, user.id, name=kw.pop("name", "Netflix"), category_id=cat.id, amount=monto,
                                  due_day=due_day, **kw)
    r.created_at = creado
    session.flush()
    return r


def _ingreso_fijo(session, user, due_day, monto=2250, **kw):
    r = rs.save_recurring_income(session, user.id, name=kw.pop("name", "Sueldo"), amount=monto, due_day=due_day, **kw)
    r.created_at = CREADO
    session.flush()
    return r


# ---- vencimientos ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("due_day, year, month, esperado", [
    (15, 2026, 11, dt.date(2026, 11, 15)),
    (rs.FIN_DE_MES, 2026, 11, dt.date(2026, 11, 30)),
    (rs.FIN_DE_MES, 2026, 2, dt.date(2026, 2, 28)),
    (rs.FIN_DE_MES, 2028, 2, dt.date(2028, 2, 29)),  # bisiesto
    (31, 2026, 2, dt.date(2026, 2, 28)),
    (31, 2026, 4, dt.date(2026, 4, 30)),
    (5, 2026, 12, dt.date(2026, 12, 5)),
])
def test_fecha_vencimiento(due_day, year, month, esperado):
    assert rs.fecha_vencimiento(due_day, year, month) == esperado


def test_describir_vencimiento():
    assert rs.describir_vencimiento(15) == "Quincena (día 15)"
    assert rs.describir_vencimiento(rs.FIN_DE_MES) == "Fin de mes"
    assert rs.describir_vencimiento(5) == "Día 5"


# ---- generación ------------------------------------------------------------------------------------------------------
def test_genera_solo_lo_vencido_y_como_pendiente(session, user, cat):
    vencido = _gasto_fijo(session, user, cat, 5)
    _gasto_fijo(session, user, cat, 15, name="Gimnasio")  # vence el 15: aún no

    creados = rs.generar_vencidos(session, user.id, HOY)

    assert [(t.description, t.status, t.date, t.gasto_type, t.source) for t in creados] == [
        ("Netflix", "pendiente", dt.date(2026, 11, 5), "fijo", "recurrente"),
    ]
    assert creados[0].recurring_expense_id == vencido.id


def test_no_duplica_aunque_se_ejecute_dos_veces(session, user, cat):
    _gasto_fijo(session, user, cat, 5)
    rs.generar_vencidos(session, user.id, HOY)
    assert rs.generar_vencidos(session, user.id, HOY) == []
    assert rs.generar_vencidos(session, user.id, HOY + dt.timedelta(days=1)) == []
    assert session.query(Transaction).count() == 1


def test_omitido_no_se_vuelve_a_generar_ese_mes(session, user, cat):
    _gasto_fijo(session, user, cat, 5)
    tx = rs.generar_vencidos(session, user.id, HOY)
    tx = [t for t in tx if t.date.month == 11][0]
    rs.omitir(session, user.id, tx.id)
    assert [t for t in rs.generar_vencidos(session, user.id, HOY) if t.date.month == 11] == []


def test_recupera_el_mes_anterior_si_el_bot_estuvo_apagado(session, user, cat):
    _gasto_fijo(session, user, cat, rs.FIN_DE_MES, name="Alquiler", created_at=dt.datetime(2026, 10, 1, 12, 0))
    creados = rs.generar_vencidos(session, user.id, dt.date(2026, 11, 2))
    assert [t.date for t in creados] == [dt.date(2026, 10, 31)]


def test_no_genera_vencimientos_anteriores_a_su_creacion(session, user, cat):
    _gasto_fijo(session, user, cat, 5, created_at=dt.datetime(2026, 11, 6, 15, 0))
    assert rs.generar_vencidos(session, user.id, HOY) == []


def test_creado_de_noche_en_lima_cuenta_ese_mismo_dia(session, user, cat):
    # 07/11 a las 21:00 en Lima = 08/11 02:00 UTC: el vencimiento del día 7 sí corresponde.
    _gasto_fijo(session, user, cat, 7, created_at=dt.datetime(2026, 11, 8, 2, 0))
    assert [t.date for t in rs.generar_vencidos(session, user.id, HOY)] == [HOY]


def test_pausado_no_genera(session, user, cat):
    _gasto_fijo(session, user, cat, 5, active=False)
    assert rs.generar_vencidos(session, user.id, HOY) == []


def test_auto_confirm_se_confirma_solo(session, user, cat):
    _gasto_fijo(session, user, cat, 5, auto_confirm=True)
    [tx] = [t for t in rs.generar_vencidos(session, user.id, HOY) if t.date.month == 11]
    assert tx.status == "confirmado"


def test_ingreso_fijo_y_balance(session, user, cat):
    _ingreso_fijo(session, user, 15)
    _ingreso_fijo(session, user, rs.FIN_DE_MES, name="Sueldo fin de mes")
    creados = rs.generar_vencidos(session, user.id, dt.date(2026, 11, 15))
    nov = [t for t in creados if t.date.month == 11]
    assert [(t.kind, t.description, t.status) for t in nov] == [("ingreso", "Sueldo", "pendiente")]

    inicio, fin = dt.date(2026, 11, 1), dt.date(2026, 11, 30)
    assert calcular_balance(session, user.id, inicio, fin).ingresos == 0  # pendiente: no cuenta
    rs.confirmar(session, user.id, nov[0].id)
    assert calcular_balance(session, user.id, inicio, fin).ingresos == Decimal("2250")


# ---- confirmar / cambiar monto / omitir ------------------------------------------------------------------------------
def test_confirmar_con_otro_monto(session, user, cat):
    _gasto_fijo(session, user, cat, 5)
    [tx] = [t for t in rs.generar_vencidos(session, user.id, HOY) if t.date.month == 11]
    res = rs.confirmar(session, user.id, tx.id, amount=49.9)
    assert (res.transaction.status, res.transaction.amount) == ("confirmado", Decimal("49.90"))
    with pytest.raises(ConfigError):
        rs.confirmar(session, user.id, tx.id)  # ya no está pendiente


def test_pendientes_listados(session, user, cat):
    _gasto_fijo(session, user, cat, 5)
    _gasto_fijo(session, user, cat, 6, name="Spotify", auto_confirm=True)
    rs.generar_vencidos(session, user.id, HOY)
    assert {t.description for t in rs.list_pendientes(session, user.id)} == {"Netflix"}


def test_proximos_vencimientos_cruzando_el_mes(session, user, cat):
    _gasto_fijo(session, user, cat, 2, name="Luz")
    _ingreso_fijo(session, user, rs.FIN_DE_MES)
    proximos = rs.proximos_vencimientos(session, user.id, dt.date(2026, 11, 30), dt.date(2026, 12, 6))
    assert [(f, n) for f, n, _, _ in proximos] == [(dt.date(2026, 11, 30), "Sueldo"), (dt.date(2026, 12, 2), "Luz")]


# ---- CRUD del panel --------------------------------------------------------------------------------------------------
def test_validaciones(session, user, cat):
    with pytest.raises(ConfigError):
        rs.save_recurring_expense(session, user.id, name="X", category_id=cat.id, amount=0, due_day=5)
    with pytest.raises(ConfigError):
        rs.save_recurring_expense(session, user.id, name="X", category_id=cat.id, amount=10, due_day=32)
    with pytest.raises(ConfigError):
        rs.save_recurring_expense(session, user.id, name=" ", category_id=cat.id, amount=10, due_day=5)
    with pytest.raises(ConfigError):
        rs.save_recurring_expense(session, user.id, name="X", category_id=999, amount=10, due_day=5)


def test_editar_y_borrar_conserva_los_movimientos(session, user, cat):
    r = _gasto_fijo(session, user, cat, 5)
    rs.save_recurring_expense(session, user.id, recurring_id=r.id, name="Netflix Premium", category_id=cat.id,
                              amount=60, due_day=rs.QUINCENA)
    assert (r.name, r.amount, r.due_day) == ("Netflix Premium", Decimal("60.00"), 15)

    rs.generar_vencidos(session, user.id, dt.date(2026, 11, 20))
    rs.delete_recurring_expense(session, user.id, r.id)
    assert session.query(RecurringExpense).count() == 0
    assert session.query(Transaction).filter(Transaction.recurring_expense_id.is_(None)).count() >= 1

    i = _ingreso_fijo(session, user, 15)
    rs.generar_vencidos(session, user.id, dt.date(2026, 11, 20))
    rs.delete_recurring_income(session, user.id, i.id)
    assert session.query(RecurringIncome).count() == 0


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
    for modulo in (handlers, jobs):
        monkeypatch.setattr(modulo, "get_session", _sesion_de_prueba)
    monkeypatch.setattr(jobs, "hoy", lambda: HOY)
    monkeypatch.setattr(handlers, "hoy", lambda: HOY)
    monkeypatch.setattr(jobs, "settings", SimpleNamespace(allowed_chat_ids=frozenset({1})))


def test_aviso_diario_con_botones(session, user, cat, bot):
    _gasto_fijo(session, user, cat, 7)
    _gasto_fijo(session, user, cat, 6, name="Spotify", monto=20, auto_confirm=True)
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    asyncio.run(jobs.revisar_recurrentes(context))

    enviados = {c.args[1]: c.kwargs["reply_markup"] for c in context.bot.send_message.call_args_list}
    assert "📅 Hoy vence Netflix: S/ 45 (Entretenimiento). ¿Lo registro?" in enviados
    assert enviados["📅 Hoy vence Netflix: S/ 45 (Entretenimiento). ¿Lo registro?"] is not None
    auto = [t for t in enviados if t.startswith("✅ Registré automáticamente Spotify")]
    assert auto and enviados[auto[0]] is None  # ya confirmado: sin botones

    context.bot.send_message.reset_mock()
    asyncio.run(jobs.revisar_recurrentes(context))  # segunda vez el mismo día: nada nuevo
    context.bot.send_message.assert_not_called()


def test_botones_confirmar_y_omitir(session, user, cat, bot):
    _gasto_fijo(session, user, cat, 5)
    _gasto_fijo(session, user, cat, 6, name="Gimnasio")
    txs = {t.description: t for t in rs.generar_vencidos(session, user.id, HOY) if t.date.month == 11}

    ok = FakeUpdate(data=f"rec_ok:{txs['Netflix'].id}")
    asyncio.run(handlers.on_confirm(ok, SimpleNamespace(user_data={})))
    assert txs["Netflix"].status == "confirmado"
    assert ok.callback_query.edit_message_text.call_args.args[0].startswith("Listo. Registrado S/ 45 de Netflix (gasto fijo)")

    asyncio.run(handlers.on_skip(FakeUpdate(data=f"rec_skip:{txs['Gimnasio'].id}"), SimpleNamespace(user_data={})))
    assert txs["Gimnasio"].status == "omitido"

    otra_vez = FakeUpdate(data=f"rec_ok:{txs['Gimnasio'].id}")
    asyncio.run(handlers.on_confirm(otra_vez, SimpleNamespace(user_data={})))
    assert "ya estaba omitido" in otra_vez.callback_query.edit_message_text.call_args.args[0]


class FakeBot:
    """send_message devuelve un mensaje con message_id creciente, como Telegram."""

    def __init__(self):
        self.enviados = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.enviados.append((text, reply_markup))
        return SimpleNamespace(message_id=100 + len(self.enviados))


def _respuesta(texto, a_mensaje):
    update = FakeUpdate(text=texto)
    update.message.reply_to_message = SimpleNamespace(message_id=a_mensaje) if a_mensaje else None
    return update


def _responder(update, context):
    """on_amount_reply corta los demás handlers con ApplicationHandlerStop cuando atiende el mensaje."""
    try:
        asyncio.run(handlers.on_amount_reply(update, context))
    except handlers.ApplicationHandlerStop:
        return True
    return False


def test_cambiar_monto_respondiendo_a_la_pregunta(session, user, cat, bot):
    _gasto_fijo(session, user, cat, 5)
    [tx] = [t for t in rs.generar_vencidos(session, user.id, HOY) if t.date.month == 11]
    context = SimpleNamespace(user_data={}, chat_data={}, bot=FakeBot())

    asyncio.run(handlers.on_change_amount(FakeUpdate(data=f"rec_amt:{tx.id}"), context))
    assert context.bot.enviados[0][0].startswith("¿Cuál fue el monto real de Netflix?")
    pregunta = 101

    assert _responder(_respuesta("abc", pregunta), context)  # monto ilegible: vuelve a preguntar
    assert tx.status == "pendiente"
    assert _responder(_respuesta("52.50", 102), context)

    assert (tx.status, tx.amount) == ("confirmado", Decimal("52.50"))


def test_un_texto_que_no_responde_a_la_pregunta_sigue_su_camino(session, user, cat, bot):
    _gasto_fijo(session, user, cat, 5)
    [tx] = [t for t in rs.generar_vencidos(session, user.id, HOY) if t.date.month == 11]
    context = SimpleNamespace(user_data={}, chat_data={}, bot=FakeBot())
    asyncio.run(handlers.on_change_amount(FakeUpdate(data=f"rec_amt:{tx.id}"), context))

    assert not _responder(_respuesta("almuerzo 25", None), context)  # texto libre normal: no lo atiende
    assert not _responder(_respuesta("25", 999), context)  # respuesta a otro mensaje: tampoco
    context.user_data.clear()  # lo que haría otro flujo: no afecta a la pregunta pendiente
    assert _responder(_respuesta("47", 101), context)
    assert tx.amount == Decimal("47.00")


def test_cambiar_monto_de_dos_recurrentes_a_la_vez(session, user, cat, bot):
    _gasto_fijo(session, user, cat, 5, name="Luz")
    _gasto_fijo(session, user, cat, 6, name="Agua")
    txs = {t.description: t for t in rs.generar_vencidos(session, user.id, HOY) if t.date.month == 11}
    context = SimpleNamespace(user_data={}, chat_data={}, bot=FakeBot())

    asyncio.run(handlers.on_change_amount(FakeUpdate(data=f"rec_amt:{txs['Luz'].id}"), context))   # pregunta 101
    asyncio.run(handlers.on_change_amount(FakeUpdate(data=f"rec_amt:{txs['Agua'].id}"), context))  # pregunta 102
    _responder(_respuesta("30", 102), context)
    _responder(_respuesta("60", 101), context)

    assert (txs["Luz"].amount, txs["Agua"].amount) == (Decimal("60.00"), Decimal("30.00"))


def test_reactivar_un_pausado_no_genera_los_meses_en_pausa(session, user, cat):
    r = _gasto_fijo(session, user, cat, 20, created_at=dt.datetime(2026, 9, 1, 12, 0), active=False)
    rs.save_recurring_expense(session, user.id, recurring_id=r.id, name="Netflix", category_id=cat.id, amount=45,
                              due_day=20, active=True)
    assert rs.generar_vencidos(session, user.id, HOY) == []  # ni el 20/10 ni nada anterior
    assert [t.date for t in rs.generar_vencidos(session, user.id, dt.date(2026, 11, 20))] == [dt.date(2026, 11, 20)]


def test_pendientes_command(session, user, cat, bot):
    vacio = FakeUpdate(text="/pendientes")
    asyncio.run(handlers.pendientes_command(vacio, SimpleNamespace(user_data={})))
    assert "No tienes recurrentes pendientes" in vacio.message.reply_text.call_args.args[0]

    _gasto_fijo(session, user, cat, 5)
    rs.generar_vencidos(session, user.id, HOY)
    con = FakeUpdate(text="/pendientes")
    asyncio.run(handlers.pendientes_command(con, SimpleNamespace(user_data={})))
    textos = [c.args[0] for c in con.message.reply_text.call_args_list]
    assert "📅 Venció el 05/11 Netflix: S/ 45 (Entretenimiento). ¿Lo registro?" in textos


def test_programar_sin_job_queue_no_rompe():
    jobs.programar(SimpleNamespace(job_queue=None))
