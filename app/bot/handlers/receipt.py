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

En los dos caminos el importe se intenta leer de la foto (`app/services/ocr.py`).
Si se lee, el paso del monto ofrece "Usar S/ X de la foto" y el pie de foto sin
importe lo usa directamente. Si no hay clave de API configurada, el bot pide el
monto a mano como antes.

Cada paso tiene "⬅️ Atrás": la pila de pasos vive en `user_data["pasos"]`.

Si la subcategoría que quieres no existe todavía, puedes crearla ahí mismo
escribiendo su nombre ("➕ Nueva subcategoría"). Queda creada de verdad (para
que el histórico y los reportes la reconozcan), pero SIN presupuesto propio
— así que no "aplica" a nada hasta que tú, desde el panel de configuración,
le asignes un monto; hasta entonces es solo una etiqueta más para ordenar
gastos, sin afectar alertas de presupuesto.
"""
from __future__ import annotations

import asyncio
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

from app.services.tiempo import hoy
from app.bot.formatting import money
from app.bot.handlers.natural_language import handle_expense_text
from app.db import get_session
from app.models import Category, Subcategory
from app.services import config as config_service
from app.services import ocr
from app.services import storage
from app.services.budgets import category_budget_statuses
from app.services.montos import parse_monto
from app.services.transactions import ValidationError, create_transaction
from app.services.users import get_or_create_user_for_chat

CHOOSING_CATEGORY, CHOOSING_SUBCATEGORY, ENTERING_NEW_SUBCATEGORY_NAME, ENTERING_AMOUNT, CONFIRMING = range(5)

ESTADO_DE_PASO = {
    "categoria": CHOOSING_CATEGORY,
    "subcategoria": CHOOSING_SUBCATEGORY,
    "nueva_sub": ENTERING_NEW_SUBCATEGORY_NAME,
    "monto": ENTERING_AMOUNT,
    "confirmar": CONFIRMING,
}
BOTON_ATRAS = InlineKeyboardButton("⬅️ Atrás", callback_data="receipt_back")


async def _save_photo(update: Update) -> tuple[int, str, bytes]:
    """Descarga la foto de mayor resolución y la sube a Supabase Storage.
    Devuelve (user_id, key dentro del bucket, bytes de la imagen)."""
    photo = update.message.photo[-1]
    file = await photo.get_file()

    with get_session() as session:
        user = get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario")
        user_id = user.id

    data = bytes(await file.download_as_bytearray())
    filename = f"{int(time.time())}_{file.file_unique_id}.jpg"
    key = storage.upload_receipt(user_id, filename, data)
    return user_id, key, data


async def _leer_monto(data: bytes) -> float | None:
    """La lectura de la foto llama a la API y es síncrona: se ejecuta en un hilo para no bloquear el bot."""
    return await asyncio.to_thread(ocr.extraer_monto, data)


def _destino_label(d: dict) -> str:
    cat = d["category_name"]
    sub = d.get("subcategory_name")
    return f"{cat} → {sub}" if sub else cat


async def _mostrar(update: Update, texto: str, teclado: InlineKeyboardMarkup | None = None) -> None:
    """Edita el mensaje si viene de un botón; responde si viene de un texto."""
    if update.callback_query:
        await update.callback_query.edit_message_text(texto, reply_markup=teclado)
    else:
        await update.message.reply_text(texto, reply_markup=teclado)


async def _pantalla(nombre: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Dibuja un paso del flujo y devuelve su estado de conversación. No toca la pila."""
    d = context.user_data
    if nombre == "categoria":
        with get_session() as session:
            categorias = config_service.list_categories(session, d["user_id"])
            botones = [[InlineKeyboardButton(c.name, callback_data=f"receipt_cat:{c.id}")] for c in categorias]
        aviso = f"\n\nLeí {money(d['monto_foto'])} en el comprobante." if d.get("monto_foto") else ""
        await _mostrar(
            update,
            "📷 Recibí la foto y la guardé como comprobante." + aviso + "\n\nSelecciona la categoría a la que se asigna el gasto:",
            InlineKeyboardMarkup(botones),
        )
    elif nombre == "subcategoria":
        with get_session() as session:
            subcategorias = config_service.list_subcategories(session, d["category_id"])
            botones = [[InlineKeyboardButton(s.name, callback_data=f"receipt_sub:{s.id}")] for s in subcategorias]
        botones.append([InlineKeyboardButton("➕ Nueva subcategoría", callback_data="receipt_sub:new")])
        botones.append([InlineKeyboardButton("(Sin subcategoría)", callback_data="receipt_sub:none")])
        botones.append([BOTON_ATRAS])
        await _mostrar(update, f"Categoría: {d['category_name']}.\n\nAhora elige la subcategoría:", InlineKeyboardMarkup(botones))
    elif nombre == "nueva_sub":
        await _mostrar(update, "¿Cómo se llama la nueva subcategoría?", InlineKeyboardMarkup([[BOTON_ATRAS]]))
    elif nombre == "monto":
        filas = []
        if d.get("monto_foto"):
            filas.append([InlineKeyboardButton(f"Usar {money(d['monto_foto'])} de la foto", callback_data="receipt_ocr")])
        filas.append([BOTON_ATRAS])
        await _mostrar(
            update,
            f"Indica el monto gastado en {_destino_label(d)} (solo el número, en soles):",
            InlineKeyboardMarkup(filas),
        )
    else:  # confirmar
        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Confirmar", callback_data="receipt_confirm"),
                InlineKeyboardButton("Cancelar", callback_data="receipt_cancel"),
            ],
            [BOTON_ATRAS],
        ])
        await _mostrar(
            update,
            f"Voy a registrar {money(d['amount'])} en {_destino_label(d)} para hoy, con la foto como comprobante. ¿Confirmar?",
            teclado,
        )
    return ESTADO_DE_PASO[nombre]


async def _avanzar(nombre: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("pasos", []).append(nombre)
    return await _pantalla(nombre, update, context)


# ------------------------------------------------- CON pie de foto: vía NLU
async def on_photo_with_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id, receipt_path, data = await _save_photo(update)
    monto_foto = await _leer_monto(data)
    context.user_data["user_id"] = user_id
    context.user_data["nlu_receipt_path"] = receipt_path
    await handle_expense_text(update, context, update.message.caption or "", monto_foto=monto_foto)


# --------------------------------------------- SIN pie de foto: vía botones
async def start_photo_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    user_id, receipt_path, data = await _save_photo(update)
    context.user_data.update(user_id=user_id, receipt_path=receipt_path, monto_foto=await _leer_monto(data), pasos=[])
    return await _avanzar("categoria", update, context)


async def on_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split(":", 1)[1])

    with get_session() as session:
        category = session.get(Category, category_id)
        # Un botón viejo puede apuntar a una categoría desactivada o, en teoría, de otro usuario.
        if category is None or category.user_id != context.user_data.get("user_id") or not category.is_active:
            await query.edit_message_text("Esa categoría ya no existe. Elige otra, o vuelve a enviar la foto.")
            return CHOOSING_CATEGORY
        category_name = category.name

    context.user_data.update(category_id=category_id, category_name=category_name, subcategory_name=None)
    return await _avanzar("subcategoria", update, context)


async def on_subcategory_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    raw = query.data.split(":", 1)[1]

    if raw == "new":
        return await _avanzar("nueva_sub", update, context)

    if raw == "none":
        context.user_data["subcategory_name"] = None
    else:
        with get_session() as session:
            subcategory = session.get(Subcategory, int(raw)) if raw.isdigit() else None
            if subcategory is None or subcategory.category_id != context.user_data["category_id"] or not subcategory.is_active:
                await query.edit_message_text("Esa subcategoría ya no existe. Elige otra.")
                return CHOOSING_SUBCATEGORY
            context.user_data["subcategory_name"] = subcategory.name

    return await _avanzar("monto", update, context)


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
        "desde el panel de configuración; no afecta ninguna alerta de presupuesto mientras tanto."
    )
    return await _avanzar("monto", update, context)


async def on_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount = parse_monto(update.message.text)
    if amount is None:
        await update.message.reply_text("No pude leer ese monto. Escribe solo el número, por ejemplo: 35 o 1,500")
        return ENTERING_AMOUNT

    context.user_data["amount"] = amount
    return await _avanzar("confirmar", update, context)


async def on_ocr_accepted(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """El botón "Usar S/ X de la foto": el importe leído pasa a ser el monto."""
    query = update.callback_query
    await query.answer()
    monto = context.user_data.get("monto_foto")
    if monto is None:
        await query.edit_message_text("Ya no tengo el importe de la foto. Escribe el monto.")
        return ENTERING_AMOUNT
    context.user_data["amount"] = monto
    return await _avanzar("confirmar", update, context)


async def on_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Vuelve al paso anterior. En el primer paso no hay botón, así que siempre hay una pila con dos pasos o más."""
    query = update.callback_query
    await query.answer()
    pasos = context.user_data.setdefault("pasos", [])
    if len(pasos) < 2:
        return ESTADO_DE_PASO[pasos[-1]] if pasos else CHOOSING_CATEGORY
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
                gasto_type="variable",
                description=data.get("subcategory_name") or data["category_name"],
                amount=data["amount"],
                date=hoy(),
                category_name=data["category_name"],
                subcategory_name=data.get("subcategory_name"),
                source="bot_foto",
                receipt_path=data.get("receipt_path"),
            )
        except ValidationError as exc:
            # La foto ya está subida: si el gasto no se registra, no se deja como comprobante huérfano.
            if data.get("receipt_path"):
                storage.delete_receipt(data["receipt_path"])
            await query.edit_message_text(f"No se pudo registrar: {exc}")
            context.user_data.clear()
            return ConversationHandler.END

        statuses = {s.category_id: s for s in category_budget_statuses(
            session, data["user_id"], hoy().replace(day=1), hoy()
        )}
        status = statuses.get(data["category_id"])

    lines = [f"Listo. Registrado {money(data['amount'])} en {_destino_label(data)} (con comprobante)."]
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
            CHOOSING_SUBCATEGORY: [
                CallbackQueryHandler(on_subcategory_chosen, pattern=r"^receipt_sub:"),
                CallbackQueryHandler(on_back, pattern=r"^receipt_back$"),
            ],
            ENTERING_NEW_SUBCATEGORY_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_new_subcategory_name),
                CallbackQueryHandler(on_back, pattern=r"^receipt_back$"),
            ],
            ENTERING_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_amount_entered),
                CallbackQueryHandler(on_ocr_accepted, pattern=r"^receipt_ocr$"),
                CallbackQueryHandler(on_back, pattern=r"^receipt_back$"),
            ],
            CONFIRMING: [
                CallbackQueryHandler(on_confirm, pattern=r"^receipt_confirm$"),
                CallbackQueryHandler(on_cancel, pattern=r"^receipt_cancel$"),
                CallbackQueryHandler(on_back, pattern=r"^receipt_back$"),
            ],
        },
        fallbacks=[CommandHandler("cancelar", on_cancel)],
        name="registrar_gasto_foto",
    )
    return [
        photo_flow,
        MessageHandler(filters.PHOTO & filters.CAPTION, on_photo_with_caption),
    ]
