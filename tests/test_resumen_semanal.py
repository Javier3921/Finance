"""Resumen semanal: cálculo de la semana anterior (con cruce de mes y de año), cifras, texto y el caso sin
movimientos; también el envío programado y /semana. Base SQLite en memoria; Telegram simulado."""
import asyncio
import datetime as dt
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot import jobs
from app.bot.handlers import summary
from app.models import Category
from app.services import recurring as rs
from app.services.config import set_budget
from app.services.resumen import construir_resumen, semana_anterior, texto_resumen
from app.services.transactions import create_transaction

LUNES = dt.date(2026, 11, 9)


@pytest.mark.parametrize("today, esperado", [
    (dt.date(2026, 11, 9), (dt.date(2026, 11, 2), dt.date(2026, 11, 8))),    # lunes
    (dt.date(2026, 11, 15), (dt.date(2026, 11, 2), dt.date(2026, 11, 8))),   # domingo: misma semana anterior
    (dt.date(2026, 12, 2), (dt.date(2026, 11, 23), dt.date(2026, 11, 29))),
    (dt.date(2026, 11, 2), (dt.date(2026, 10, 26), dt.date(2026, 11, 1))),   # cruza el mes
    (dt.date(2027, 1, 4), (dt.date(2026, 12, 28), dt.date(2027, 1, 3))),     # cruza el año
])
def test_semana_anterior(today, esperado):
    assert semana_anterior(today) == esperado


@pytest.fixture()
def cats(session, user):
    nombres = ["Comida", "Transporte", "Salidas", "Compras"]
    for n in nombres:
        session.add(Category(user_id=user.id, name=n))
    session.flush()
    return nombres


def _gasto(session, user, cat, monto, fecha):
    create_transaction(session, user_id=user.id, kind="gasto", gasto_type="variable", description=cat, amount=monto,
                       date=fecha, category_name=cat)


def test_resumen_con_cifras(session, user, cats):
    semana = dt.date(2026, 11, 2)
    for cat, monto in [("Comida", 300), ("Transporte", 80), ("Salidas", 120), ("Compras", 50), ("Comida", 100)]:
        _gasto(session, user, cat, monto, semana)
    _gasto(session, user, "Comida", 400, dt.date(2026, 10, 27))  # semana previa
    _gasto(session, user, "Comida", 999, LUNES)  # semana en curso: no entra
    create_transaction(session, user_id=user.id, kind="ingreso", description="Sueldo", amount=2250, date=dt.date(2026, 11, 8))
    set_budget(session, session.query(Category).filter_by(name="Comida").one().id, 2026, 11, 450)

    r = construir_resumen(session, user.id, LUNES)

    assert (r.inicio, r.fin) == (dt.date(2026, 11, 2), dt.date(2026, 11, 8))
    assert r.gastado == Decimal("650")
    assert r.variacion_pct == Decimal("62.5")
    assert r.ingresos == Decimal("2250")
    assert r.top_categorias == [("Comida", Decimal("400")), ("Salidas", Decimal("120")), ("Transporte", Decimal("80"))]
    assert r.presupuestos_en_riesgo == [("Comida", "alerta", 88)]

    texto = texto_resumen(r)
    assert "Gastaste S/ 650 (+62% vs. la semana previa)." in texto
    assert "Ingresos: S/ 2,250." in texto
    assert "  1. Comida: S/ 400" in texto
    assert "  Comida: 88% (en alerta)" in texto


def test_semana_sin_movimientos_en_una_linea(session, user):
    texto = texto_resumen(construir_resumen(session, user.id, LUNES))
    assert texto == "📊 Semana del 02/11 al 08/11: sin movimientos registrados."


def test_incluye_proximos_vencimientos_y_pendientes(session, user, cats):
    cat = session.query(Category).filter_by(name="Comida").one()
    r1 = rs.save_recurring_expense(session, user.id, name="Netflix", category_id=cat.id, amount=45, due_day=12)
    r2 = rs.save_recurring_expense(session, user.id, name="Gimnasio", category_id=cat.id, amount=90, due_day=5)
    r1.created_at = r2.created_at = dt.datetime(2026, 11, 1, 12, 0)
    session.flush()
    rs.generar_vencidos(session, user.id, LUNES)  # Gimnasio del 05/11 queda pendiente

    texto = texto_resumen(construir_resumen(session, user.id, LUNES))

    assert "📅 Vencen esta semana:\n  12/11 Netflix: S/ 45" in texto
    assert "Tienes 1 recurrente(s) sin confirmar: /pendientes" in texto


# ---- bot -------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def bot(monkeypatch, session, user):
    @contextmanager
    def _sesion_de_prueba():
        yield session

    user.telegram_chat_id = "1"
    session.flush()
    for modulo in (jobs, summary):
        monkeypatch.setattr(modulo, "get_session", _sesion_de_prueba)
        monkeypatch.setattr(modulo, "hoy", lambda: LUNES)
    monkeypatch.setattr(jobs, "settings", SimpleNamespace(allowed_chat_ids=frozenset({1, 999})))  # 999: sin usuario


def test_envio_programado_solo_a_chats_con_usuario(session, user, bot):
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(jobs.enviar_resumen_semanal(context))
    [llamada] = context.bot.send_message.call_args_list
    assert llamada.args[0] == 1
    assert llamada.args[1].startswith("📊 Semana del 02/11 al 08/11")


def test_comando_semana(session, user, bot):
    update = SimpleNamespace(
        message=SimpleNamespace(reply_text=AsyncMock()), effective_chat=SimpleNamespace(id=1),
        effective_user=SimpleNamespace(first_name="Test"),
    )
    asyncio.run(summary.semana_command(update, SimpleNamespace(user_data={})))
    assert update.message.reply_text.call_args.args[0].startswith("📊 Semana del 02/11 al 08/11")
