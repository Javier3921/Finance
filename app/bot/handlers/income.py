"""Ingresos: el flujo /ingreso (monto -> concepto -> fecha -> confirmar) y la confirmación de los ingresos escritos en
texto libre ("me pagaron 1500 del freelance").

Un ingreso no lleva categoría ni presupuesto: solo concepto, monto y fecha. Igual que en /gasto, cada paso tiene
"⬅️ Atrás" y la pila de pasos vive en `user_data["pasos"]`.
"""
from __future__ import annotations

import datetime as dt
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from app.bot.formatting import money
from app.db import get_session
from app.services.balance import calcular_balance
from app.services.montos import parse_monto
from app.services.nlp import INCOME_CONCEPTS, parse_income_text
from app.services.tiempo import hoy
from app.services.transactions import ValidationError, create_transaction
from app.services.users import get_or_create_user_for_chat

ENTERING_AMOUNT, CHOOSING_CONCEPT, CHOOSING_DATE, CONFIRMING = range(4)
ESTADO_DE_PASO = {"monto": ENTERING_AMOUNT, "concepto": CHOOSING_CONCEPT, "fecha": CHOOSING_DATE, "confirmar": CONFIRMING}
BOTON_ATRAS = InlineKeyboardButton("⬅️ Atrás", callback_data="ing_back")
_FECHA_RE = re.compile(r"^\s*(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2}|\d{4}))?\s*$")
_NLU_KEY = "ing_nlu"


def parse_fecha(texto: str, *, today: dt.date | None = None) -> dt.date | None:
    """'dd/mm' o 'dd/mm/aaaa' (también con '-'). Sin año: el año actual. None si no es una fecha válida o es futura."""
    today = today or hoy()
    m = _FECHA_RE.match(texto or "")
    if not m:
        return None
    dia, mes, anio = int(m.group(1)), int(m.group(2)), m.group(3)
    year = today.year if anio is None else (2000 + int(anio) if len(anio) == 2 else int(anio))
    try:
        fecha = dt.date(year, mes, dia)
    except ValueError:
        return None
    return fecha if fecha <= today else None


def _cuando(fecha: dt.date) -> str:
    if fecha == hoy():
        return "hoy"
    if fecha == hoy() - dt.timedelta(days=1):
        return "ayer"
    return fecha.strftime("%d/%m/%Y")


async def _mostrar(update: Update, texto: str, teclado: InlineKeyboardMarkup | None = None) -> None:
    if update.callback_query:
        await update.callback_query.edit_message_text(texto, reply_markup=teclado)
    else:
        await update.message.reply_text(texto, reply_markup=teclado)


async def _pantalla(nombre: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    d = context.user_data
    if nombre == "monto":
        await _mostrar(update, "💰 ¿Cuánto recibiste? (solo el número, en soles)")
    elif nombre == "concepto":
        botones = [[InlineKeyboardButton(c, callback_data=f"ing_concept:{c}")] for c in INCOME_CONCEPTS]
        botones.append([BOTON_ATRAS])
        await _mostrar(
            update, f"Ingreso de {money(d['amount'])}. ¿De qué es? Elige una opción o escribe el concepto:",
            InlineKeyboardMarkup(botones),
        )
    elif nombre == "fecha":
        teclado = InlineKeyboardMarkup([
            [InlineKeyboardButton("Hoy", callback_data="ing_date:0"), InlineKeyboardButton("Ayer", callback_data="ing_date:1")],
            [BOTON_ATRAS],
        ])
        await _mostrar(update, "¿Cuándo lo recibiste? Elige o escribe la fecha (dd/mm):", teclado)
    else:  # confirmar
        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Confirmar", callback_data="ing_confirm"),
                InlineKeyboardButton("Cancelar", callback_data="ing_cancel"),
            ],
            [BOTON_ATRAS],
        ])
        fecha = dt.date.fromisoformat(d["date"])
        await _mostrar(
            update, f"Voy a registrar un ingreso de {money(d['amount'])} ({d['concept']}) para {_cuando(fecha)}. ¿Confirmar?",
            teclado,
        )
    return ESTADO_DE_PASO[nombre]


async def _avanzar(nombre: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("pasos", []).append(nombre)
    return await _pantalla(nombre, update, context)


def _registrar(user_id: int, amount: float, concept: str, fecha: dt.date) -> str:
    """Crea el ingreso y devuelve el mensaje de éxito con cómo va el mes."""
    with get_session() as session:
        create_transaction(
            session, user_id=user_id, kind="ingreso", description=concept, amount=amount, date=fecha, source="bot_texto",
        )
        mes = fecha.replace(day=1)
        siguiente = (mes + dt.timedelta(days=32)).replace(day=1)
        b = calcular_balance(session, user_id, mes, siguiente - dt.timedelta(days=1))
    return (
        f"Listo. Registrado un ingreso de {money(amount)} ({concept}).\n"
        f"Ingresos del mes: {money(b.ingresos)} · Balance: {money(b.balance)}"
    )


# ------------------------------------------------------------------------------------------------------- /ingreso
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    with get_session() as session:
        user = get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario")
        context.user_data.update(user_id=user.id, pasos=[])
    return await _avanzar("monto", update, context)


async def on_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount = parse_monto(update.message.text)
    if amount is None:
        await update.message.reply_text("No pude leer ese monto. Escribe solo el número, por ejemplo: 1500 o 1,500.50")
        return ENTERING_AMOUNT
    context.user_data["amount"] = amount
    return await _avanzar("concepto", update, context)


async def on_concept_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["concept"] = query.data.split(":", 1)[1]
    return await _avanzar("fecha", update, context)


async def on_concept_typed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    concepto = (update.message.text or "").strip()[:200]
    if not concepto:
        return CHOOSING_CONCEPT
    context.user_data["concept"] = concepto
    return await _avanzar("fecha", update, context)


async def on_date_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    dias = int(query.data.split(":", 1)[1])
    context.user_data["date"] = (hoy() - dt.timedelta(days=dias)).isoformat()
    return await _avanzar("confirmar", update, context)


async def on_date_typed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    fecha = parse_fecha(update.message.text)
    if fecha is None:
        await update.message.reply_text("No pude leer esa fecha. Escríbela como dd/mm (por ejemplo 15/10); no puede ser futura.")
        return CHOOSING_DATE
    context.user_data["date"] = fecha.isoformat()
    return await _avanzar("confirmar", update, context)


async def on_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    pasos = context.user_data.setdefault("pasos", [])
    if len(pasos) < 2:
        return ESTADO_DE_PASO[pasos[-1]] if pasos else ENTERING_AMOUNT
    pasos.pop()
    return await _pantalla(pasos[-1], update, context)


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = context.user_data
    try:
        texto = _registrar(d["user_id"], d["amount"], d["concept"], dt.date.fromisoformat(d["date"]))
    except ValidationError as exc:
        texto = f"No se pudo registrar: {exc}"
    await query.edit_message_text(texto)
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


# ------------------------------------------------------------------------------------------------- texto libre
async def try_handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Si `text` es un ingreso ("me pagaron 1500 del freelance"), pide confirmación y devuelve True; si no, False y el
    texto sigue como gasto."""
    parsed = parse_income_text(text)
    if parsed is None:
        return False
    with get_session() as session:
        user = get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario")
        user_id = user.id
    context.user_data[_NLU_KEY] = {
        "user_id": user_id, "amount": parsed.amount, "concept": parsed.concept, "date": parsed.date.isoformat(),
    }
    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("Confirmar", callback_data="ing_nlu_confirm"),
        InlineKeyboardButton("Cancelar", callback_data="ing_nlu_cancel"),
    ]])
    await update.message.reply_text(
        f"Voy a registrar un ingreso de {money(parsed.amount)} ({parsed.concept}) para {_cuando(parsed.date)}. ¿Confirmar?",
        reply_markup=teclado,
    )
    return True


async def on_nlu_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    d = context.user_data.pop(_NLU_KEY, None)
    if d is None:
        await query.edit_message_text("Esta confirmación ya expiró. Escribe el ingreso de nuevo o usa /ingreso.")
        return
    try:
        texto = _registrar(d["user_id"], d["amount"], d["concept"], dt.date.fromisoformat(d["date"]))
    except ValidationError as exc:
        texto = f"No se pudo registrar: {exc}"
    await query.edit_message_text(texto)


async def on_nlu_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.pop(_NLU_KEY, None)
    await query.edit_message_text("Cancelado. No se registró nada. Si era un gasto, usa /gasto.")


def build_handlers() -> list:
    conversacion = ConversationHandler(
        entry_points=[CommandHandler("ingreso", start)],
        states={
            ENTERING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_amount_entered)],
            CHOOSING_CONCEPT: [
                CallbackQueryHandler(on_concept_chosen, pattern=r"^ing_concept:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_concept_typed),
                CallbackQueryHandler(on_back, pattern=r"^ing_back$"),
            ],
            CHOOSING_DATE: [
                CallbackQueryHandler(on_date_chosen, pattern=r"^ing_date:\d+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_date_typed),
                CallbackQueryHandler(on_back, pattern=r"^ing_back$"),
            ],
            CONFIRMING: [
                CallbackQueryHandler(on_confirm, pattern=r"^ing_confirm$"),
                CallbackQueryHandler(on_cancel, pattern=r"^ing_cancel$"),
                CallbackQueryHandler(on_back, pattern=r"^ing_back$"),
            ],
        },
        fallbacks=[CommandHandler("cancelar", on_cancel)],
        name="registrar_ingreso",
    )
    return [
        conversacion,
        CallbackQueryHandler(on_nlu_confirm, pattern=r"^ing_nlu_confirm$"),
        CallbackQueryHandler(on_nlu_cancel, pattern=r"^ing_nlu_cancel$"),
    ]
