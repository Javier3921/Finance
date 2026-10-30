"""Flujo guiado /gasto: tipo -> categoría -> subcategoría -> monto -> confirmar.

Las categorías y subcategorías se leen de la base de datos en cada paso
(no están fijas en el código), así que si el usuario agrega/renombra una
categoría desde el panel de configuración, el bot la ofrece de inmediato.

Cada paso tiene "⬅️ Atrás". La pila de pasos vive en `user_data["pasos"]`: al volver,
se quita el paso actual y se vuelve a dibujar el anterior tal como era.
"""
from __future__ import annotations

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from app.services.tiempo import hoy
from app.bot.formatting import money
from app.db import get_session
from app.models import Category, Subcategory
from app.services.budgets import category_budget_statuses
from app.services.montos import parse_monto
from app.services.transactions import ValidationError, create_transaction
from app.services.users import get_or_create_user_for_chat

CHOOSING_TYPE, CHOOSING_CATEGORY, CHOOSING_SUBCATEGORY, ENTERING_AMOUNT, CONFIRMING = range(5)

ESTADO_DE_PASO = {
    "tipo": CHOOSING_TYPE,
    "categoria": CHOOSING_CATEGORY,
    "subcategoria": CHOOSING_SUBCATEGORY,
    "monto": ENTERING_AMOUNT,
    "confirmar": CONFIRMING,
}
BOTON_ATRAS = InlineKeyboardButton("⬅️ Atrás", callback_data="gasto_back")


def _destino(d: dict) -> str:
    cat = d["category_name"]
    subcat = d.get("subcategory_name")
    return f"{cat} → {subcat}" if subcat else cat


async def _mostrar(update: Update, texto: str, teclado: InlineKeyboardMarkup | None = None) -> None:
    """Edita el mensaje si viene de un botón; responde si viene de un texto (el monto)."""
    if update.callback_query:
        await update.callback_query.edit_message_text(texto, reply_markup=teclado)
    else:
        await update.message.reply_text(texto, reply_markup=teclado)


async def _pantalla(nombre: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Dibuja un paso y devuelve su estado. No toca la pila."""
    d = context.user_data
    if nombre == "tipo":
        teclado = InlineKeyboardMarkup([
            [InlineKeyboardButton("Gasto fijo", callback_data="gasto_type:fijo")],
            [InlineKeyboardButton("Gasto variable", callback_data="gasto_type:variable")],
        ])
        await _mostrar(update, "Claro. ¿Qué tipo de gasto quieres registrar?", teclado)
    elif nombre == "categoria":
        with get_session() as session:
            categorias = session.scalars(
                select(Category).where(Category.user_id == d["user_id"], Category.is_active.is_(True)).order_by(Category.sort_order)
            ).all()
            botones = [[InlineKeyboardButton(c.name, callback_data=f"gasto_cat:{c.id}")] for c in categorias]
        botones.append([BOTON_ATRAS])
        await _mostrar(update, "¿Qué categoría corresponde?", InlineKeyboardMarkup(botones))
    elif nombre == "subcategoria":
        with get_session() as session:
            subcategorias = session.scalars(
                select(Subcategory).where(Subcategory.category_id == d["category_id"], Subcategory.is_active.is_(True)).order_by(Subcategory.sort_order)
            ).all()
            botones = [[InlineKeyboardButton(s.name, callback_data=f"gasto_subcat:{s.id}")] for s in subcategorias]
        botones.append([InlineKeyboardButton("(Sin subcategoría)", callback_data="gasto_subcat:none")])
        botones.append([BOTON_ATRAS])
        await _mostrar(update, "¿Qué tipo de gasto, más específicamente?", InlineKeyboardMarkup(botones))
    elif nombre == "monto":
        await _mostrar(
            update,
            f"Categoría: {_destino(d)}.\n¿Cuánto gastaste? (solo el número, en soles)",
            InlineKeyboardMarkup([[BOTON_ATRAS]]),
        )
    else:  # confirmar
        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Confirmar", callback_data="gasto_confirm"),
                InlineKeyboardButton("Cancelar", callback_data="gasto_cancel"),
            ],
            [BOTON_ATRAS],
        ])
        await _mostrar(update, f"Voy a registrar {money(d['amount'])} en {_destino(d)} para hoy. ¿Confirmar?", teclado)
    return ESTADO_DE_PASO[nombre]


async def _avanzar(nombre: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("pasos", []).append(nombre)
    return await _pantalla(nombre, update, context)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data["pasos"] = []
    return await _avanzar("tipo", update, context)


async def on_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["gasto_type"] = query.data.split(":", 1)[1]

    with get_session() as session:
        user = get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario")
        context.user_data["user_id"] = user.id
    return await _avanzar("categoria", update, context)


async def on_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split(":", 1)[1])

    with get_session() as session:
        category = session.get(Category, category_id)
        if category is None or category.user_id != context.user_data["user_id"] or not category.is_active:
            await query.edit_message_text("Esa categoría ya no existe. Usa /gasto para empezar de nuevo.")
            return CHOOSING_CATEGORY
        category_name = category.name
        tiene_subcategorias = session.scalars(
            select(Subcategory.id).where(Subcategory.category_id == category_id, Subcategory.is_active.is_(True))
        ).first() is not None

    context.user_data.update(category_id=category_id, category_name=category_name, subcategory_name=None)
    if not tiene_subcategorias:
        return await _avanzar("monto", update, context)
    return await _avanzar("subcategoria", update, context)


async def on_subcategory_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    raw = query.data.split(":", 1)[1]

    if raw == "none":
        context.user_data["subcategory_name"] = None
    else:
        with get_session() as session:
            subcategory = session.get(Subcategory, int(raw)) if raw.isdigit() else None
            if subcategory is None or subcategory.category_id != context.user_data["category_id"] or not subcategory.is_active:
                await query.edit_message_text("Esa subcategoría ya no existe. Usa /gasto para empezar de nuevo.")
                return CHOOSING_SUBCATEGORY
            context.user_data["subcategory_name"] = subcategory.name

    return await _avanzar("monto", update, context)


async def on_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount = parse_monto(update.message.text)
    if amount is None:
        await update.message.reply_text("No pude leer ese monto. Escribe solo el número, por ejemplo: 35 o 1,500")
        return ENTERING_AMOUNT

    context.user_data["amount"] = amount
    return await _avanzar("confirmar", update, context)


async def on_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Vuelve al paso anterior: quita el actual de la pila y dibuja el que queda encima."""
    query = update.callback_query
    await query.answer()
    pasos = context.user_data.setdefault("pasos", [])
    if len(pasos) < 2:
        return ESTADO_DE_PASO[pasos[-1]] if pasos else CHOOSING_TYPE
    pasos.pop()
    return await _pantalla(pasos[-1], update, context)


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
                date=hoy(),
                category_name=data["category_name"],
                subcategory_name=data.get("subcategory_name"),
                source="bot_texto",
            )
        except ValidationError as exc:
            await query.edit_message_text(f"No se pudo registrar: {exc}")
            context.user_data.clear()
            return ConversationHandler.END

        statuses = {s.category_id: s for s in category_budget_statuses(
            session, data["user_id"], hoy().replace(day=1), hoy()
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
            CHOOSING_CATEGORY: [
                CallbackQueryHandler(on_category_chosen, pattern=r"^gasto_cat:"),
                CallbackQueryHandler(on_back, pattern=r"^gasto_back$"),
            ],
            CHOOSING_SUBCATEGORY: [
                CallbackQueryHandler(on_subcategory_chosen, pattern=r"^gasto_subcat:"),
                CallbackQueryHandler(on_back, pattern=r"^gasto_back$"),
            ],
            ENTERING_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_amount_entered),
                CallbackQueryHandler(on_back, pattern=r"^gasto_back$"),
            ],
            CONFIRMING: [
                CallbackQueryHandler(on_confirm, pattern=r"^gasto_confirm$"),
                CallbackQueryHandler(on_cancel, pattern=r"^gasto_cancel$"),
                CallbackQueryHandler(on_back, pattern=r"^gasto_back$"),
            ],
        },
        fallbacks=[CommandHandler("cancelar", on_cancel)],
        name="registrar_gasto",
    )
