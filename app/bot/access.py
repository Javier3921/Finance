"""Control de acceso del bot: solo atiende a los chats de ALLOWED_CHAT_IDS.

Un bot de Telegram es público — cualquiera que encuentre su @usuario puede
escribirle. Sin este filtro, `users.get_or_create_user_for_chat` le
crearía un usuario nuevo a cualquier desconocido y le dejaría registrar
movimientos en la base de datos.

Falla cerrado, igual que la contraseña del dashboard:
- ALLOWED_CHAT_IDS vacío: el bot no registra nada; a quien le escriba solo
  le responde con su propio chat_id, para poder configurarlo la primera vez.
- ALLOWED_CHAT_IDS con valores: los chats que no están en la lista se
  ignoran en silencio (sin respuesta que confirme que el bot existe).

Se registra en el grupo -1 (`app/bot/main.py`), que corre antes que todos
los demás handlers; `ApplicationHandlerStop` corta el update ahí mismo.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from app.config import settings

logger = logging.getLogger(__name__)


def is_chat_allowed(chat_id: int, allowed: frozenset[int]) -> bool:
    return chat_id in allowed


async def restrict_to_allowed_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is not None and is_chat_allowed(chat.id, settings.allowed_chat_ids):
        return  # sigue al resto de handlers

    if chat is not None:
        logger.warning("Update ignorado de un chat no autorizado: %s", chat.id)

    if update.callback_query is not None:
        await update.callback_query.answer()
    if not settings.allowed_chat_ids and update.effective_message is not None and chat is not None:
        await update.effective_message.reply_text(
            "Este bot todavía no tiene ningún chat autorizado.\n\n"
            f"Tu chat_id es: {chat.id}\n\n"
            f"Si eres el dueño, agrega ALLOWED_CHAT_IDS={chat.id} al .env del bot y reinícialo."
        )
    raise ApplicationHandlerStop
