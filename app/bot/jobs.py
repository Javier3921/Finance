"""Tareas programadas del bot (JobQueue de python-telegram-bot, extra `[job-queue]`), en hora de Lima.

- Recurrentes: cada día a las 08:00 y una vez al arrancar (por si el bot estuvo apagado a esa hora).
- Resumen semanal: los lunes a las 08:00 (ver `app/services/resumen.py`).

Solo se escribe a los chats de ALLOWED_CHAT_IDS que ya están vinculados a un usuario. Si el extra no está instalado,
el bot sigue funcionando sin tareas y lo avisa en el log.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from telegram.ext import Application, ContextTypes

from app.bot.handlers import recurring as recurring_handlers
from app.config import settings
from app.db import get_session
from app.models import User
from app.services import recurring as recurring_service
from app.services.resumen import construir_resumen, texto_resumen
from app.services.tiempo import LIMA, hoy

logger = logging.getLogger(__name__)

HORA_AVISOS = dt.time(8, 0, tzinfo=LIMA)
LUNES = 1  # JobQueue.run_daily: 0 = domingo … 6 = sábado (python-telegram-bot >= 20)


def _usuarios_destino() -> list[tuple[int, int]]:
    """(chat_id, user_id) de los chats permitidos que ya tienen usuario."""
    with get_session() as session:
        filas = []
        for chat_id in sorted(settings.allowed_chat_ids):
            user = session.scalar(select(User).where(User.telegram_chat_id == str(chat_id)))
            if user is not None:
                filas.append((chat_id, user.id))
    return filas


async def revisar_recurrentes(context: ContextTypes.DEFAULT_TYPE) -> None:
    today = hoy()
    for chat_id, user_id in _usuarios_destino():
        with get_session() as session:
            creados = recurring_service.generar_vencidos(session, user_id, today)
            avisos = [(tx.id, tx.status, recurring_handlers.texto_aviso(tx, today)) for tx in creados]
        for tx_id, status, texto in avisos:
            teclado = recurring_handlers.teclado(tx_id) if status == "pendiente" else None
            try:
                await context.bot.send_message(chat_id, texto, reply_markup=teclado)
            except Exception:  # noqa: BLE001 - el movimiento ya quedó pendiente; /pendientes lo muestra igual
                logger.warning("No se pudo avisar del recurrente %s al chat %s", tx_id, chat_id, exc_info=True)


async def enviar_resumen_semanal(context: ContextTypes.DEFAULT_TYPE) -> None:
    today = hoy()
    for chat_id, user_id in _usuarios_destino():
        with get_session() as session:
            texto = texto_resumen(construir_resumen(session, user_id, today))
        try:
            await context.bot.send_message(chat_id, texto)
        except Exception:  # noqa: BLE001 - se puede pedir igual con /semana
            logger.warning("No se pudo enviar el resumen semanal al chat %s", chat_id, exc_info=True)


def programar(application: Application) -> None:
    jq = application.job_queue
    if jq is None:
        logger.warning("JobQueue no disponible: instala python-telegram-bot[job-queue]. Sin avisos de recurrentes ni resumen semanal.")
        print("AVISO: falta python-telegram-bot[job-queue]; no habrá avisos de recurrentes ni resumen semanal.")
        return
    jq.run_once(revisar_recurrentes, when=15, name="recurrentes_al_arrancar")
    jq.run_daily(revisar_recurrentes, time=HORA_AVISOS, name="recurrentes_diario")
    jq.run_daily(enviar_resumen_semanal, time=HORA_AVISOS, days=(LUNES,), name="resumen_semanal")
