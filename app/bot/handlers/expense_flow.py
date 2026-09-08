"""Flujo guiado /gasto: tipo -> categoría -> subcategoría -> monto -> confirmar.

Las categorías y subcategorías se leen de la base de datos en cada paso
(no están fijas en el código), así que si el usuario agrega/renombra una
categoría desde el panel de configuración, el bot la ofrece de inmediato.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from app.bot.formatting import money
from app.db import get_session
from app.models import Category, Subcategory
from app.services.budgets import category_budget_statuses
from app.services.transactions import ValidationError, create_transaction
from app.services.users import get_or_create_user_for_chat

CHOOSING_TYPE, CHOOSING_CATEGORY, CHOOSING_SUBCATEGORY, ENTERING_AMOUNT, CONFIRMING = range(5)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton("Gasto fijo", callback_data="gasto_type:fijo")],
        [InlineKeyboardButton("Gasto variable", callback_data="gasto_type:variable")],
    ]
    await update.message.reply_text(
        "Claro. ¿Qué tipo de gasto quieres registrar?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_TYPE


async def on_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["gasto_type"] = query.data.split(":", 1)[1]

    with get_session() as session:
        user = get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario")
        context.user_data["user_id"] = user.id
        categories = session.scalars(
            select(Category).where(Category.user_id == user.id, Category.is_active.is_(True)).order_by(Category.sort_order)
        ).all()
        buttons = [[InlineKeyboardButton(c.name, callback_data=f"gasto_cat:{c.id}")] for c in categories]

    await query.edit_message_text("¿Qué categoría corresponde?", reply_markup=InlineKeyboardMarkup(buttons))
    return CHOOSING_CATEGORY


async def on_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split(":", 1)[1])
    context.user_data["category_id"] = category_id

    with get_session() as session:
        category = session.get(Category, category_id)
        context.user_data["category_name"] = category.name
        subcategories = session.scalars(
            select(Subcategory).where(Subcategory.category_id == category_id, Subcategory.is_active.is_(True)).order_by(Subcategory.sort_order)
        ).all()

    if not subcategories:
        context.user_data["subcategory_name"] = None
        await query.edit_message_text(f"Categoría: {context.user_data['category_name']}.\n¿Cuánto gastaste? (solo el número, en soles)")
        return ENTERING_AMOUNT

    buttons = [[InlineKeyboardButton(s.name, callback_data=f"gasto_subcat:{s.id}")] for s in subcategories]
    buttons.append([InlineKeyboardButton("(Sin subcategoría)", callback_data="gasto_subcat:none")])
    await query.edit_message_text("¿Qué tipo de gasto, más específicamente?", reply_markup=InlineKeyboardMarkup(buttons))
    return CHOOSING_SUBCATEGORY


async def on_subcategory_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    raw = query.data.split(":", 1)[1]

    if raw == "none":
        context.user_data["subcategory_name"] = None
    else:
        with get_session() as session:
            subcategory = session.get(Subcategory, int(raw))
            context.user_data["subcategory_name"] = subcategory.name

    await query.edit_message_text("¿Cuánto gastaste? (solo el número, en soles)")
    return ENTERING_AMOUNT


async def on_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(",", ".").replace("S/", "").strip()
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("No pude leer ese monto. Escribe solo el número, por ejemplo: 35")
        return ENTERING_AMOUNT

    context.user_data["amount"] = amount
    cat = context.user_data["category_name"]
    subcat = context.user_data.get("subcategory_name")
    destino = f"{cat} → {subcat}" if subcat else cat

    keyboard = [[
        InlineKeyboardButton("Confirmar", callback_data="gasto_confirm"),
        InlineKeyboardButton("Cancelar", callback_data="gasto_cancel"),
    ]]
    await update.message.reply_text(
        f"Voy a registrar {money(amount)} en {destino} para hoy. ¿Confirmar?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CONFIRMING


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = context.user_data

    with get_session() as session:
        try:
            create_transaction(
                session,
                user_id=data["user_id"],
                kind="gasto",
                gasto_type=data["gasto_type"],
                description=data.get("subcategory_name") or data["category_name"],
                amount=data["amount"],
                date=dt.date.today(),
                category_name=data["category_name"],
                subcategory_name=data.get("subcategory_name"),
                source="bot_texto",
            )
        except ValidationError as exc:
            await query.edit_message_text(f"No se pudo registrar: {exc}")
            context.user_data.clear()
            return ConversationHandler.END

        statuses = {s.category_id: s for s in category_budget_statuses(
            session, data["user_id"], dt.date.today().replace(day=1), dt.date.today()
        )}
        status = statuses.get(data["category_id"])

    lines = [f"Listo. Registrado {money(data['amount'])} en {data['category_name']}."]
    if status and status.budget > 0:
        lines.append(f"Llevas {money(status.spent)} de {money(status.budget)} este mes ({int(status.pct_used)}%).")

    await query.edit_message_text("\n".join(lines))
    context.user_data.clear()
    return ConversationHandler.END


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Cancelado. No se registró nada.")
    else:
        await update.message.reply_text("Cancelado. No se registró nada.")
    context.user_data.clear()
    return ConversationHandler.END


def build_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("gasto", start)],
        states={
            CHOOSING_TYPE: [CallbackQueryHandler(on_type_chosen, pattern=r"^gasto_type:")],
            CHOOSING_CATEGORY: [CallbackQueryHandler(on_category_chosen, pattern=r"^gasto_cat:")],
            CHOOSING_SUBCATEGORY: [CallbackQueryHandler(on_subcategory_chosen, pattern=r"^gasto_subcat:")],
            ENTERING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_amount_entered)],
            CONFIRMING: [
                CallbackQueryHandler(on_confirm, pattern=r"^gasto_confirm$"),
                CallbackQueryHandler(on_cancel, pattern=r"^gasto_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancelar", on_cancel)],
        name="registrar_gasto",
    )
