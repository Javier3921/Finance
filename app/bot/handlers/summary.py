"""Comandos de consulta: /start, /presupuesto, /resumen."""
from __future__ import annotations

import datetime as dt

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.formatting import budget_line, money
from app.db import get_session
from app.services.budgets import category_budget_statuses
from app.services.transactions import list_transactions
from app.services.users import get_or_create_user_for_chat

COMMANDS_HELP = (
    "Puedo ayudarte a llevar tus finanzas. Comandos disponibles:\n\n"
    "/gasto — registrar un gasto paso a paso\n"
    "/presupuesto — cuánto llevas gastado por categoría este mes\n"
    "/resumen — resumen del mes\n\n"
    "También puedes escribirme directo, por ejemplo:\n"
    "\"Hoy gasté 35 soles en almuerzo\""
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Hola, {update.effective_user.first_name}. {COMMANDS_HELP}")


async def presupuesto_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = dt.date.today()
    month_start = today.replace(day=1)

    with get_session() as session:
        user = get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario")
        statuses = category_budget_statuses(session, user.id, month_start, today)

    if not statuses:
        await update.message.reply_text("Todavía no hay presupuestos ni gastos registrados este mes.")
        return

    lines = [f"Presupuestos de {today.strftime('%B %Y')}:\n"]
    lines += [budget_line(s) for s in statuses]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def resumen_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = dt.date.today()
    month_start = today.replace(day=1)

    with get_session() as session:
        user = get_or_create_user_for_chat(session, update.effective_chat.id, update.effective_user.first_name or "Usuario")
        gastos = list_transactions(session, user.id, month_start, today, kind="gasto")
        ahorros_inversion = [
            t for t in list_transactions(session, user.id, month_start, today) if t.kind in ("ahorro", "inversion")
        ]

    total_gastado = sum(float(t.amount) for t in gastos)
    total_ahorro_inv = sum(float(t.amount) for t in ahorros_inversion)
    fijos = sum(float(t.amount) for t in gastos if t.gasto_type == "fijo")
    variables = sum(float(t.amount) for t in gastos if t.gasto_type == "variable")

    lines = [
        f"Resumen de {today.strftime('%B %Y')}:",
        f"Gasto fijo: {money(fijos)}",
        f"Gasto variable: {money(variables)}",
        f"Total gastado: {money(total_gastado)}",
        f"Ahorro + inversión: {money(total_ahorro_inv)}",
        f"\nMovimientos registrados: {len(gastos) + len(ahorros_inversion)}",
    ]
    await update.message.reply_text("\n".join(lines))
