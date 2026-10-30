"""Registro por texto libre: 'Hoy gasté 35 soles en almuerzo' -> confirmación.

También es el motor que usa `receipt.py` para el paso "¿cuánto fue?" después
de recibir una foto de comprobante: `handle_expense_text` no le importa si
el texto vino de un mensaje normal o de un pie de foto. Si hay una foto
pendiente (`nlu_receipt_path` en user_data), queda adjunta al resultado; si el
texto no trae un monto, la foto se descarta en vez de quedar pegada al
siguiente mensaje. Si la foto trae el importe (leído por `app/services/ocr.py`)
y el pie de foto no, ese importe se usa para confirmar.

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

from app.services.tiempo import hoy
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
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Confirmar", callback_data="nlu_confirm"),
            InlineKeyboardButton("Cancelar", callback_data="nlu_cancel"),
        ],
        [InlineKeyboardButton("⬅️ Cambiar categoría", callback_data="nlu_back_cat")],
    ])


def _confirm_text(amount: float, destino: str, cuando: str, has_receipt: bool) -> str:
    base = f"Voy a registrar {money(amount)} en {destino} para {cuando}"
    return f"{base}, con la foto como comprobante. ¿Confirmar?" if has_receipt else f"{base}. ¿Confirmar?"


def _cuando(fecha_iso: str) -> str:
    fecha = dt.date.fromisoformat(fecha_iso)
    return "hoy" if fecha == hoy() else fecha.strftime("%d/%m")


def _categorias_keyboard(user_id: int) -> InlineKeyboardMarkup:
    with get_session() as session:
        categories = session.scalars(
            select(Category).where(Category.user_id == user_id, Category.is_active.is_(True)).order_by(Category.sort_order)
        ).all()
        buttons = [[InlineKeyboardButton(c.name, callback_data=f"nlu_cat:{c.id}")] for c in categories]
    return InlineKeyboardMarkup(buttons)


async def _descartar_foto(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quita la foto pendiente y la borra del bucket: no se deja como comprobante huérfano."""
    receipt_path = context.user_data.get("nlu_receipt_path")
    if receipt_path:
        storage.delete_receipt(receipt_path)
    for key in _PENDING_KEYS:
        context.user_data.pop(key, None)


async def handle_expense_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, monto_foto: float | None = None
) -> None:
    """Parsea `text` como un gasto y avanza el flujo de confirmación. `monto_foto` es el importe leído de la
    foto, si lo hubo; el del texto tiene prioridad."""
    hubo_foto = bool(context.user_data.get("nlu_receipt_path"))
    parsed = parse_expense_text(text)
    if parsed is None and monto_foto is None:
        await _descartar_foto(context)
        aviso = " La foto no se guardó: vuelve a enviarla con el monto en el pie de foto, o usa /gasto." if hubo_foto else ""
        await update.message.reply_text(
            "No reconocí ningún monto ahí. Usa /gasto para el paso a paso, o escribe algo como "
            f"'gasté 35 en almuerzo'.{aviso}"
        )
        return

    amount = parsed.amount if parsed else monto_foto
    category = parsed.category if parsed else None
    subcategory = parsed.subcategory if parsed else None
    fecha = parsed.date if parsed else hoy()
    has_receipt = bool(context.user_data.get("nlu_receipt_path"))

    with get_session() as session:
        user = get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario")
        user_id = user.id
        context.user_data["user_id"] = user_id
        context.user_data["nlu_amount"] = amount
        context.user_data["nlu_date"] = fecha.isoformat()
        context.user_data["nlu_subcategory"] = subcategory

    if category is None:
        origen = " en la foto" if parsed is None else ""
        await update.message.reply_text(
            f"Detecté {money(amount)}{origen}, pero no supe en qué categoría. ¿Cuál es?",
            reply_markup=_categorias_keyboard(user_id),
        )
        return

    context.user_data["nlu_category"] = category
    destino = f"{category} → {subcategory}" if subcategory else category
    await update.message.reply_text(
        _confirm_text(amount, destino, _cuando(fecha.isoformat()), has_receipt),
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
        if category is None or category.user_id != context.user_data.get("user_id") or not category.is_active:
            await query.edit_message_text("Esa opción ya no existe. Escribe el gasto de nuevo.")
            return
        category_name = category.name

    data = context.user_data
    if "nlu_amount" not in data:
        await query.edit_message_text("Esta confirmación ya expiró. Intenta de nuevo.")
        return
    data["nlu_category"] = category_name
    subcategory = data.get("nlu_subcategory")
    destino = f"{category_name} → {subcategory}" if subcategory else category_name
    await query.edit_message_text(
        _confirm_text(data["nlu_amount"], destino, _cuando(data["nlu_date"]), bool(data.get("nlu_receipt_path"))),
        reply_markup=_confirm_keyboard(),
    )


async def on_back_to_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Vuelve a la lista de categorías desde la confirmación, por si se eligió una que no era."""
    query = update.callback_query
    await query.answer()
    user_id = context.user_data.get("user_id")
    if user_id is None or "nlu_amount" not in context.user_data:
        await query.edit_message_text("Esta confirmación ya expiró. Intenta de nuevo.")
        return
    context.user_data.pop("nlu_category", None)
    await query.edit_message_text(
        f"Detecté {money(context.user_data['nlu_amount'])}, ¿en qué categoría va?",
        reply_markup=_categorias_keyboard(user_id),
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
    fecha = dt.date.fromisoformat(data["nlu_date"])

    with get_session() as session:
        try:
            create_transaction(
                session,
                user_id=data["user_id"],
                kind="gasto",
                gasto_type="variable",
                description=data.get("nlu_subcategory") or category_name,
                amount=amount,
                date=fecha,
                category_name=category_name,
                subcategory_name=data.get("nlu_subcategory"),
                source="bot_foto" if receipt_path else "bot_texto",
                receipt_path=receipt_path,
            )
        except ValidationError as exc:
            # La foto ya está subida: si el gasto no se registra, no queda como comprobante huérfano.
            if receipt_path:
                storage.delete_receipt(receipt_path)
            await query.edit_message_text(f"No se pudo registrar: {exc}")
            return
        finally:
            for key in _PENDING_KEYS:
                data.pop(key, None)

        # Presupuesto del mes del movimiento (no del mes de hoy): un gasto de ayer en el día 1 cae en el mes anterior.
        mes = fecha.replace(day=1)
        siguiente_mes = (mes + dt.timedelta(days=32)).replace(day=1)
        fin = min(hoy(), siguiente_mes - dt.timedelta(days=1))
        statuses = {s.category_name: s for s in category_budget_statuses(session, data["user_id"], mes, fin)}
        status = statuses.get(category_name)

    lines = [f"Listo. Registrado {money(amount)} en {category_name}" + (" (con comprobante)." if receipt_path else ".")]
    if status and status.budget > 0:
        cuando = "este mes" if mes == hoy().replace(day=1) else f"el mes de {mes.strftime('%m/%Y')}"
        lines.append(f"Llevas {money(status.spent)} de {money(status.budget)} {cuando} ({int(status.pct_used)}%).")
    await query.edit_message_text("\n".join(lines))


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await _descartar_foto(context)
    await query.edit_message_text("Cancelado. No se registró nada.")


def build_handlers() -> list:
    return [
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_free_text),
        CallbackQueryHandler(on_category_chosen, pattern=r"^nlu_cat:"),
        CallbackQueryHandler(on_back_to_category, pattern=r"^nlu_back_cat$"),
        CallbackQueryHandler(on_confirm, pattern=r"^nlu_confirm$"),
        CallbackQueryHandler(on_cancel, pattern=r"^nlu_cancel$"),
    ]
