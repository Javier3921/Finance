"""Recurrentes en el bot: el aviso con [Confirmar] [Cambiar monto] [Omitir este mes], `/pendientes` y el paso de
escribir el monto real. La lógica (qué vence, sin duplicados) está en `app/services/recurring.py`; los avisos diarios
los programa `app/bot/jobs.py`.

"Cambiar monto" no es una conversación: el bot manda una pregunta con ForceReply y guarda en `chat_data` qué
movimiento corresponde a ESA pregunta. Solo una respuesta a ese mensaje cuenta como monto, y el handler va en un grupo
anterior a los flujos (ver `main.py`). Así un /gasto o /ingreso abandonado no se queda con el monto, el monto no se
traga el texto libre, ningún `user_data.clear()` de otro flujo lo borra y cada recurrente tiene su propia pregunta.
"""
from __future__ import annotations

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.bot.formatting import money
from app.db import get_session
from app.models import Transaction
from app.services import recurring as recurring_service
from app.services.config import ConfigError
from app.services.montos import parse_monto
from app.services.tiempo import hoy
from app.services.users import get_or_create_user_for_chat

_PREGUNTAS = "rec_preguntas_monto"  # chat_data: {message_id de la pregunta: tx_id}
_MAX_PREGUNTAS = 20


def teclado(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Confirmar", callback_data=f"rec_ok:{tx_id}"),
         InlineKeyboardButton("Cambiar monto", callback_data=f"rec_amt:{tx_id}")],
        [InlineKeyboardButton("Omitir este mes", callback_data=f"rec_skip:{tx_id}")],
    ])


def _detalle(tx: Transaction) -> str:
    if tx.kind == "ingreso":
        return f"{tx.description}: {money(tx.amount)} (ingreso fijo)"
    cat = tx.category.name if tx.category else "sin categoría"
    return f"{tx.description}: {money(tx.amount)} ({cat})"


def texto_aviso(tx: Transaction, today=None) -> str:
    """El mensaje del aviso diario. Si el movimiento se confirmó solo (`auto_confirm`), solo informa."""
    today = today or hoy()
    if tx.status == "confirmado":
        return f"✅ Registré automáticamente {_detalle(tx)}. Si no corresponde, usa /deshacer."
    cuando = "Hoy vence" if tx.date == today else f"Venció el {tx.date.strftime('%d/%m')}"
    if tx.kind == "ingreso":
        cuando = "Hoy toca" if tx.date == today else f"Tocaba el {tx.date.strftime('%d/%m')}"
    return f"📅 {cuando} {_detalle(tx)}. ¿Lo registro?"


def _user_id(update: Update) -> int:
    with get_session() as session:
        return get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario").id


def _resultado_texto(res: recurring_service.Resultado) -> str:
    tx = res.transaction
    if res.accion == "omitido":
        return f"Omitido este mes: {tx.description}. No cuenta en los totales."
    tipo = "ingreso" if tx.kind == "ingreso" else "gasto fijo"
    return f"Listo. Registrado {money(tx.amount)} de {tx.description} ({tipo}) el {tx.date.strftime('%d/%m')}."


async def _aplicar(update: Update, accion, **kw) -> None:
    query = update.callback_query
    await query.answer()
    tx_id = int(query.data.split(":", 1)[1])
    user_id = _user_id(update)
    try:
        with get_session() as session:
            texto = _resultado_texto(accion(session, user_id, tx_id, **kw))
    except ConfigError as exc:
        texto = f"{exc}."
    await query.edit_message_text(texto)


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _aplicar(update, recurring_service.confirmar)


async def on_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _aplicar(update, recurring_service.omitir)


async def _preguntar_monto(context: ContextTypes.DEFAULT_TYPE, chat_id: int, tx_id: int, texto: str) -> None:
    pregunta = await context.bot.send_message(
        chat_id, texto, reply_markup=ForceReply(selective=True, input_field_placeholder="Monto, p. ej. 45.90"),
    )
    preguntas = context.chat_data.setdefault(_PREGUNTAS, {})
    preguntas[pregunta.message_id] = tx_id
    for viejo in sorted(preguntas)[:-_MAX_PREGUNTAS]:
        preguntas.pop(viejo, None)


async def on_change_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    tx_id = int(query.data.split(":", 1)[1])
    with get_session() as session:
        tx = session.get(Transaction, tx_id)
        vigente = tx is not None and tx.status == "pendiente"
        nombre = tx.description if tx else ""
    if not vigente:
        await query.edit_message_text("Ese movimiento ya no está pendiente. Usa /pendientes para ver los que quedan.")
        return
    await _preguntar_monto(
        context, update.effective_chat.id, tx_id,
        f"¿Cuál fue el monto real de {nombre}? Responde a este mensaje con el número. "
        "Mientras tanto sigue pendiente (también puedes usar los botones de arriba).",
    )


async def on_amount_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Grupo anterior a los flujos: si el mensaje responde a una pregunta de monto, la atiende y corta el resto de
    handlers; si no, no hace nada y el mensaje sigue su camino normal."""
    respondido = update.message.reply_to_message
    preguntas = context.chat_data.get(_PREGUNTAS, {})
    if respondido is None or respondido.message_id not in preguntas:
        return
    tx_id = preguntas[respondido.message_id]
    amount = parse_monto(update.message.text)
    if amount is None:
        await _preguntar_monto(
            context, update.effective_chat.id, tx_id,
            "No pude leer ese monto. Responde a este mensaje solo con el número, por ejemplo: 45 o 1,200.50",
        )
        raise ApplicationHandlerStop
    preguntas.pop(respondido.message_id, None)
    user_id = _user_id(update)
    try:
        with get_session() as session:
            texto = _resultado_texto(recurring_service.confirmar(session, user_id, tx_id, amount=amount))
    except ConfigError as exc:
        texto = f"{exc}."
    await update.message.reply_text(texto)
    raise ApplicationHandlerStop


async def pendientes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user_id(update)
    with get_session() as session:
        avisos = [(tx.id, texto_aviso(tx)) for tx in recurring_service.list_pendientes(session, user_id)]
    if not avisos:
        await update.message.reply_text("No tienes recurrentes pendientes de confirmar. 👌")
        return
    for tx_id, texto in avisos:
        await update.message.reply_text(texto, reply_markup=teclado(tx_id))


# Va en un grupo propio, antes de los flujos (ver main.py).
reply_handler = MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, on_amount_reply)


def build_handlers() -> list:
    return [
        CommandHandler("pendientes", pendientes_command),
        CallbackQueryHandler(on_confirm, pattern=r"^rec_ok:\d+$"),
        CallbackQueryHandler(on_skip, pattern=r"^rec_skip:\d+$"),
        CallbackQueryHandler(on_change_amount, pattern=r"^rec_amt:\d+$"),
    ]
