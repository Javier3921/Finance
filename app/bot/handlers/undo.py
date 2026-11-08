"""/deshacer: muestra el último movimiento registrado y lo borra solo si se pulsa "Sí, borrar". Si viene de un
recurrente no se borra, queda omitido ese mes (ver `transactions.undo_transaction`).

El botón lleva el id del movimiento que se mostró: un botón viejo nunca borra otro movimiento distinto del que se vio.
La foto del comprobante se borra del bucket después de que el borrado en la base quedó confirmado.
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from app.bot.formatting import money
from app.db import get_session
from app.models import Transaction
from app.services import storage
from app.services.transactions import ValidationError, last_transaction, undo_transaction
from app.services.users import get_or_create_user_for_chat

logger = logging.getLogger(__name__)

TIPO = {"gasto": "Gasto", "ingreso": "Ingreso", "ahorro": "Ahorro", "inversion": "Inversión"}
ESTADO = {"pendiente": " (pendiente)", "omitido": " (omitido)"}


def describir(tx: Transaction) -> str:
    """'Gasto de S/ 35 en Comida → Almuerzo, 07/11/2026 (con foto)'."""
    if tx.category is not None:
        destino = f"en {tx.category.name}" + (f" → {tx.subcategory.name}" if tx.subcategory else "")
    else:
        destino = f"({tx.description})"
    foto = " (con foto)" if tx.receipt_path else ""
    return f"{TIPO.get(tx.kind, tx.kind)} de {money(tx.amount)} {destino}, {tx.date.strftime('%d/%m/%Y')}{foto}{ESTADO.get(tx.status, '')}"


async def deshacer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_session() as session:
        user = get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario")
        tx = last_transaction(session, user.id)
        texto = describir(tx) if tx else None
        tx_id = tx.id if tx else None

    if tx_id is None:
        await update.message.reply_text("No hay movimientos registrados que deshacer.")
        return
    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("Sí, borrar", callback_data=f"undo_yes:{tx_id}"),
        InlineKeyboardButton("No", callback_data="undo_no"),
    ]])
    await update.message.reply_text(f"El último movimiento es:\n{texto}\n\n¿Lo borro? No se puede recuperar.", reply_markup=teclado)


async def on_undo_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    tx_id = int(query.data.split(":", 1)[1])
    try:
        with get_session() as session:
            user = get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario")
            tx = session.get(Transaction, tx_id)
            texto = describir(tx) if tx is not None and tx.user_id == user.id else None
            accion, receipt_path = undo_transaction(session, user.id, tx_id)
    except ValidationError as exc:
        await query.edit_message_text(f"{exc}. Usa /deshacer para ver el último.")
        return

    if receipt_path and not storage.delete_receipt(receipt_path):
        logger.warning("Movimiento %s borrado, pero no se pudo borrar su foto %s", tx_id, receipt_path)
    if accion == "omitido":
        await query.edit_message_text(
            f"Deshecho: {texto}. Es un recurrente, así que queda como omitido este mes (no cuenta en los totales "
            "y no se vuelve a generar).\nUsa /deshacer otra vez para el anterior."
        )
        return
    await query.edit_message_text(f"Borrado: {texto}.\nUsa /deshacer otra vez para borrar el anterior.")


async def on_undo_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Listo, no borré nada.")


def build_handlers() -> list:
    return [
        CommandHandler("deshacer", deshacer_command),
        CallbackQueryHandler(on_undo_yes, pattern=r"^undo_yes:\d+$"),
        CallbackQueryHandler(on_undo_no, pattern=r"^undo_no$"),
    ]
