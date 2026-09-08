"""Registro de gasto a partir de una foto de comprobante.

Dos caminos, según si la foto viene con pie de foto (caption) o no:

- CON pie de foto (ej. mandas la foto y escribes "35 en almuerzo"): se
  interpreta el texto con el mismo parser de lenguaje natural que el
  registro por texto — `natural_language.handle_expense_text`.
- SIN pie de foto: NUNCA se adivina la categoría. Se abre un flujo guiado
  por botones — categoría → subcategoría → monto — igual que /gasto, para
  no repetir el problema de que una palabra suelta en la respuesta ("gasté
  120 en el gimnasio") dispare una categoría equivocada por coincidencia de
  palabra clave.

Si la subcategoría que quieres no existe todavía, puedes crearla ahí mismo
escribiendo su nombre ("➕ Nueva subcategoría"). Queda creada de verdad (para
que el histórico y los reportes la reconozcan), pero SIN presupuesto propio
— así que no "aplica" a nada hasta que tú, desde el panel de configuración,
le asignes un monto; hasta entonces es solo una etiqueta más para ordenar
gastos, sin afectar alertas de presupuesto.

Fase actual: sin OCR (cero costo, cero configuración nueva) — ver nota en
`natural_language.py` sobre el siguiente paso si se quiere lectura
automática del monto/comercio desde la imagen.
"""
from __future__ import annotations

import datetime as dt
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.bot.formatting import money
from app.bot.handlers.natural_language import handle_expense_text
from app.db import get_session
from app.models import Category, Subcategory
from app.services import config as config_service
from app.services import storage
from app.services.budgets import category_budget_statuses
from app.services.transactions import ValidationError, create_transaction
from app.services.users import get_or_create_user_for_chat

CHOOSING_CATEGORY, CHOOSING_SUBCATEGORY, ENTERING_NEW_SUBCATEGORY_NAME, ENTERING_AMOUNT, CONFIRMING = range(5)


async def _save_photo(update: Update) -> tuple[int, str]:
    """Descarga la foto de mayor resolución y la sube a Supabase Storage.
    Devuelve (user_id, key dentro del bucket)."""
    photo = update.message.photo[-1]
    file = await photo.get_file()

    with get_session() as session:
        user = get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario")
        user_id = user.id

    data = await file.download_as_bytearray()
    filename = f"{int(time.time())}_{file.file_unique_id}.jpg"
    key = storage.upload_receipt(user_id, filename, bytes(data))
    return user_id, key


def _destino_label(context: ContextTypes.DEFAULT_TYPE) -> str:
    cat = context.user_data["category_name"]
    sub = context.user_data.get("subcategory_name")
    return f"{cat} → {sub}" if sub else cat


# ------------------------------------------------- CON pie de foto: vía NLU
async def on_photo_with_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id, receipt_path = await _save_photo(update)
    context.user_data["user_id"] = user_id
    context.user_data["nlu_receipt_path"] = receipt_path
    await handle_expense_text(update, context, update.message.caption)


# --------------------------------------------- SIN pie de foto: vía botones
async def start_photo_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    user_id, receipt_path = await _save_photo(update)
    context.user_data["user_id"] = user_id
    context.user_data["receipt_path"] = receipt_path

    with get_session() as session:
        categories = config_service.list_categories(session, user_id)
        buttons = [[InlineKeyboardButton(c.name, callback_data=f"receipt_cat:{c.id}")] for c in categories]

    await update.message.reply_text(
        "📷 Recibí la foto y la guardé como comprobante.\n\nSelecciona la categoría a la que se asigna el gasto:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return CHOOSING_CATEGORY


async def on_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split(":", 1)[1])

    with get_session() as session:
        category = session.get(Category, category_id)
        category_name = category.name
        subcats = config_service.list_subcategories(session, category_id)

    context.user_data["category_id"] = category_id
    context.user_data["category_name"] = category_name

    buttons = [[InlineKeyboardButton(s.name, callback_data=f"receipt_sub:{s.id}")] for s in subcats]
    buttons.append([InlineKeyboardButton("➕ Nueva subcategoría", callback_data="receipt_sub:new")])
    buttons.append([InlineKeyboardButton("(Sin subcategoría)", callback_data="receipt_sub:none")])

    await query.edit_message_text(
        f"Categoría: {category_name}.\n\nAhora elige la subcategoría:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return CHOOSING_SUBCATEGORY


async def on_subcategory_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    raw = query.data.split(":", 1)[1]

    if raw == "new":
        await query.edit_message_text("¿Cómo se llama la nueva subcategoría?")
        return ENTERING_NEW_SUBCATEGORY_NAME

    if raw == "none":
        context.user_data["subcategory_name"] = None
    else:
        with get_session() as session:
            subcategory = session.get(Subcategory, int(raw))
            context.user_data["subcategory_name"] = subcategory.name

    await query.edit_message_text(f"Indica el monto gastado en {_destino_label(context)} (solo el número, en soles):")
    return ENTERING_AMOUNT


async def on_new_subcategory_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Ese nombre no sirve. Escribe un nombre para la nueva subcategoría:")
        return ENTERING_NEW_SUBCATEGORY_NAME

    with get_session() as session:
        try:
            config_service.create_subcategory(session, context.user_data["category_id"], name)
        except config_service.ConfigError as exc:
            await update.message.reply_text(f"{exc}\nEscribe otro nombre:")
            return ENTERING_NEW_SUBCATEGORY_NAME

    context.user_data["subcategory_name"] = name
    await update.message.reply_text(
        f"Creé '{name}' dentro de {context.user_data['category_name']}. Por ahora no tiene presupuesto propio — "
        "queda solo como etiqueta para este gasto (y los que quieras después) hasta que le asignes un monto "
        "desde el panel de configuración; no afecta ninguna alerta de presupuesto mientras tanto.\n\n"
        f"Indica el monto gastado en {_destino_label(context)} (solo el número, en soles):"
    )
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
    keyboard = [[
        InlineKeyboardButton("Confirmar", callback_data="receipt_confirm"),
        InlineKeyboardButton("Cancelar", callback_data="receipt_cancel"),
    ]]
    await update.message.reply_text(
        f"Voy a registrar {money(amount)} en {_destino_label(context)} para hoy, con la foto como comprobante. "
        "¿Confirmar?",
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
                gasto_type="variable",
                description=data.get("subcategory_name") or data["category_name"],
                amount=data["amount"],
                date=dt.date.today(),
                category_name=data["category_name"],
                subcategory_name=data.get("subcategory_name"),
                source="bot_foto",
                receipt_path=data.get("receipt_path"),
            )
        except ValidationError as exc:
            await query.edit_message_text(f"No se pudo registrar: {exc}")
            context.user_data.clear()
            return ConversationHandler.END

        statuses = {s.category_id: s for s in category_budget_statuses(
            session, data["user_id"], dt.date.today().replace(day=1), dt.date.today()
        )}
        status = statuses.get(data["category_id"])

    lines = [f"Listo. Registrado {money(data['amount'])} en {_destino_label(context)} (con comprobante)."]
    if status and status.budget > 0:
        lines.append(f"Llevas {money(status.spent)} de {money(status.budget)} este mes ({int(status.pct_used)}%).")
    await query.edit_message_text("\n".join(lines))
    context.user_data.clear()
    return ConversationHandler.END


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    receipt_path = context.user_data.get("receipt_path")
    if receipt_path:
        storage.delete_receipt(receipt_path)  # no dejar comprobantes huérfanos en el bucket

    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Cancelado. No se registró nada.")
    else:
        await update.message.reply_text("Cancelado. No se registró nada.")
    context.user_data.clear()
    return ConversationHandler.END


def build_handlers() -> list:
    photo_flow = ConversationHandler(
        entry_points=[MessageHandler(filters.PHOTO & ~filters.CAPTION, start_photo_flow)],
        states={
            CHOOSING_CATEGORY: [CallbackQueryHandler(on_category_chosen, pattern=r"^receipt_cat:")],
            CHOOSING_SUBCATEGORY: [CallbackQueryHandler(on_subcategory_chosen, pattern=r"^receipt_sub:")],
            ENTERING_NEW_SUBCATEGORY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_new_subcategory_name)],
            ENTERING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_amount_entered)],
            CONFIRMING: [
                CallbackQueryHandler(on_confirm, pattern=r"^receipt_confirm$"),
                CallbackQueryHandler(on_cancel, pattern=r"^receipt_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancelar", on_cancel)],
        name="registrar_gasto_foto",
    )
    return [
        photo_flow,
        MessageHandler(filters.PHOTO & filters.CAPTION, on_photo_with_caption),
    ]
