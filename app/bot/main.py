"""Punto de entrada del bot de Telegram.

No se ejecuta al importar este módulo (solo bajo `if __name__ == "__main__"`),
así que se puede importar en tests o herramientas sin necesitar un token real.

Uso:
    python -m app.bot.main
Requiere TELEGRAM_BOT_TOKEN en el archivo .env (crea un bot nuevo con
@BotFather en Telegram — no reutilices uno que ya esté sirviendo otro
proyecto).
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, TypeHandler

from app.bot import access
from app.bot.handlers import expense_flow, natural_language, receipt, summary
from app.config import settings
from app.db import init_db


def build_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError(
            "Falta TELEGRAM_BOT_TOKEN. Crea un bot con @BotFather en Telegram y ponlo en el archivo .env "
            "(ver .env.example)."
        )

    application = Application.builder().token(settings.telegram_bot_token).build()

    # Grupo -1: corre antes que cualquier otro handler y corta los updates de
    # chats que no están en ALLOWED_CHAT_IDS (ver app/bot/access.py).
    application.add_handler(TypeHandler(Update, access.restrict_to_allowed_chats), group=-1)

    application.add_handler(CommandHandler("start", summary.start_command))
    application.add_handler(CommandHandler("presupuesto", summary.presupuesto_command))
    application.add_handler(CommandHandler("resumen", summary.resumen_command))

    # El flujo /gasto se registra ANTES que el handler de texto libre: mientras
    # una conversación /gasto está activa para ese chat, ConversationHandler
    # intercepta el mensaje y el handler de lenguaje natural no llega a verlo.
    application.add_handler(expense_flow.build_handler())
    for handler in receipt.build_handlers():
        application.add_handler(handler)
    for handler in natural_language.build_handlers():
        application.add_handler(handler)

    return application


def main() -> None:
    init_db()
    application = build_application()
    if not settings.allowed_chat_ids:
        print("AVISO: ALLOWED_CHAT_IDS está vacío — el bot no registrará nada; "
              "escríbele para obtener tu chat_id y agrégalo al .env.")
    print("Bot corriendo (Ctrl+C para detener)...")
    application.run_polling()


if __name__ == "__main__":
    main()
