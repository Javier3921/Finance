"""Recurrentes en el bot: el aviso con [Confirmar] [Cambiar monto] [Omitir este mes], `/pendientes` y el paso de
escribir el monto real. La lógica (qué vence, sin duplicados) está en `app/services/recurring.py`; los avisos diarios
los programa `app/bot/jobs.py`.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from app.bot.formatting import money
from app.db import get_session
from app.models import Transaction
from app.services import recurring as recurring_service
from app.services.config import ConfigError
from app.services.montos import parse_monto
from app.services.tiempo import hoy
from app.services.users import get_or_create_user_for_chat

ENTERING_AMOUNT = 0
_KEY = "rec_tx_id"


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


async def on_change_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    tx_id = int(query.data.split(":", 1)[1])
    with get_session() as session:
        tx = session.get(Transaction, tx_id)
        vigente = tx is not None and tx.status == "pendiente"
        nombre = tx.description if tx else ""
    if not vigente:
        await query.edit_message_text("Ese movimiento ya no está pendiente. Usa /pendientes para ver los que quedan.")
        return ConversationHandler.END
    context.user_data[_KEY] = tx_id
    await query.edit_message_text(f"¿Cuál fue el monto real de {nombre}? (solo el número; /cancelar para dejarlo pendiente)")
    return ENTERING_AMOUNT


async def on_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount = parse_monto(update.message.text)
    if amount is None:
        await update.message.reply_text("No pude leer ese monto. Escribe solo el número, por ejemplo: 45 o 1,200.50")
        return ENTERING_AMOUNT
    tx_id = context.user_data.pop(_KEY, None)
    user_id = _user_id(update)
    try:
        with get_session() as session:
            texto = _resultado_texto(recurring_service.confirmar(session, user_id, tx_id, amount=amount))
    except ConfigError as exc:
        texto = f"{exc}."
    await update.message.reply_text(texto)
    return ConversationHandler.END


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_KEY, None)
    await update.message.reply_text("Listo, sigue pendiente. Usa /pendientes cuando quieras confirmarlo.")
    return ConversationHandler.END


async def pendientes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user_id(update)
    with get_session() as session:
        avisos = [(tx.id, texto_aviso(tx)) for tx in recurring_service.list_pendientes(session, user_id)]
    if not avisos:
        await update.message.reply_text("No tienes recurrentes pendientes de confirmar. 👌")
        return
    for tx_id, texto in avisos:
        await update.message.reply_text(texto, reply_markup=teclado(tx_id))


def build_handlers() -> list:
    cambiar_monto = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_change_amount, pattern=r"^rec_amt:\d+$")],
        states={ENTERING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_amount_entered)]},
        fallbacks=[CommandHandler("cancelar", on_cancel)],
        name="monto_recurrente",
    )
    return [
        cambiar_monto,
        CommandHandler("pendientes", pendientes_command),
        CallbackQueryHandler(on_confirm, pattern=r"^rec_ok:\d+$"),
        CallbackQueryHandler(on_skip, pattern=r"^rec_skip:\d+$"),
    ]
