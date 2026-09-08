"""Registro por texto libre: 'Hoy gasté 35 soles en almuerzo' -> confirmación.

También es el motor que usa `receipt.py` para el paso "¿cuánto fue?" después
de recibir una foto de comprobante: `handle_expense_text` no le importa si
el texto vino de un mensaje normal o de un pie de foto — y si hay una foto
pendiente (`nlu_receipt_path` en user_data), la arrastra hasta el guardado
final sin que el resto del flujo tenga que saberlo.

Solo actúa cuando NO hay una conversación /gasto activa (ver orden de
registro de handlers en app/bot/main.py). Si el parser determinístico no
reconoce una categoría, se la pregunta antes de confirmar — nunca adivina
en silencio.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from app.bot.formatting import money
from app.db import get_session
from app.models import Category
from app.services import storage
from app.services.budgets import category_budget_statuses
from app.services.nlp import parse_expense_text
from app.services.transactions import ValidationError, create_transaction
from app.services.users import get_or_create_user_for_chat

_PENDING_KEYS = ("nlu_amount", "nlu_date", "nlu_subcategory", "nlu_category", "nlu_receipt_path")


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Confirmar", callback_data="nlu_confirm"),
        InlineKeyboardButton("Cancelar", callback_data="nlu_cancel"),
    ]])


def _confirm_text(amount: float, destino: str, cuando: str, has_receipt: bool) -> str:
    base = f"Voy a registrar {money(amount)} en {destino} para {cuando}"
    return f"{base}, con la foto como comprobante. ¿Confirmar?" if has_receipt else f"{base}. ¿Confirmar?"


async def handle_expense_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Parsea `text` como un gasto y avanza el flujo de confirmación. Si ya
    había una foto pendiente en `context.user_data['nlu_receipt_path']`
    (recibida antes de este texto), queda adjunta al resultado."""
    parsed = parse_expense_text(text)
    if parsed is None:
        await update.message.reply_text(
            "No reconocí ningún monto ahí. Usa /gasto para el paso a paso, o escribe algo como "
            "'gasté 35 en almuerzo'."
        )
        return

    has_receipt = bool(context.user_data.get("nlu_receipt_path"))

    with get_session() as session:
        user = get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario")
        context.user_data["user_id"] = user.id
        context.user_data["nlu_amount"] = parsed.amount
        context.user_data["nlu_date"] = parsed.date.isoformat()
        context.user_data["nlu_subcategory"] = parsed.subcategory

        if parsed.category is None:
            categories = session.scalars(
                select(Category).where(Category.user_id == user.id, Category.is_active.is_(True)).order_by(Category.sort_order)
            ).all()
            buttons = [[InlineKeyboardButton(c.name, callback_data=f"nlu_cat:{c.id}")] for c in categories]
            await update.message.reply_text(
                f"Detecté {money(parsed.amount)}, pero no supe en qué categoría. ¿Cuál es?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        context.user_data["nlu_category"] = parsed.category

    destino = f"{parsed.category} → {parsed.subcategory}" if parsed.subcategory else parsed.category
    cuando = "hoy" if parsed.date == dt.date.today() else parsed.date.strftime("%d/%m")
    await update.message.reply_text(
        _confirm_text(parsed.amount, destino, cuando, has_receipt),
        reply_markup=_confirm_keyboard(),
    )


async def on_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_expense_text(update, context, update.message.text)


async def on_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split(":", 1)[1])

    with get_session() as session:
        category = session.get(Category, category_id)
        category_name = category.name
        context.user_data["nlu_category"] = category_name

    amount = context.user_data.get("nlu_amount")
    has_receipt = bool(context.user_data.get("nlu_receipt_path"))
    await query.edit_message_text(
        _confirm_text(amount, category_name, "hoy", has_receipt),
        reply_markup=_confirm_keyboard(),
    )


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = context.user_data
    if "nlu_amount" not in data or "nlu_category" not in data:
        await query.edit_message_text("Esta confirmación ya expiró. Intenta de nuevo.")
        return

    amount = data["nlu_amount"]
    category_name = data["nlu_category"]
    receipt_path = data.get("nlu_receipt_path")

    with get_session() as session:
        try:
            create_transaction(
                session,
                user_id=data["user_id"],
                kind="gasto",
                gasto_type="variable",
                description=data.get("nlu_subcategory") or category_name,
                amount=amount,
                date=dt.date.fromisoformat(data["nlu_date"]),
                category_name=category_name,
                subcategory_name=data.get("nlu_subcategory"),
                source="bot_foto" if receipt_path else "bot_texto",
                receipt_path=receipt_path,
            )
        except ValidationError as exc:
            await query.edit_message_text(f"No se pudo registrar: {exc}")
            return
        finally:
            for key in _PENDING_KEYS:
                data.pop(key, None)

        statuses = {s.category_name: s for s in category_budget_statuses(
            session, data["user_id"], dt.date.today().replace(day=1), dt.date.today()
        )}
        status = statuses.get(category_name)

    lines = [f"Listo. Registrado {money(amount)} en {category_name}" + (" (con comprobante)." if receipt_path else ".")]
    if status and status.budget > 0:
        lines.append(f"Llevas {money(status.spent)} de {money(status.budget)} este mes ({int(status.pct_used)}%).")
    await query.edit_message_text("\n".join(lines))


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    receipt_path = context.user_data.get("nlu_receipt_path")
    if receipt_path:
        storage.delete_receipt(receipt_path)  # no dejar comprobantes huérfanos en el bucket
    for key in _PENDING_KEYS:
        context.user_data.pop(key, None)
    await query.edit_message_text("Cancelado. No se registró nada.")


def build_handlers() -> list:
    return [
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_free_text),
        CallbackQueryHandler(on_category_chosen, pattern=r"^nlu_cat:"),
        CallbackQueryHandler(on_confirm, pattern=r"^nlu_confirm$"),
        CallbackQueryHandler(on_cancel, pattern=r"^nlu_cancel$"),
    ]
