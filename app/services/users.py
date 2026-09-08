"""Resolución de usuario a partir del chat de Telegram.

MVP de un solo usuario: si nadie tiene aún un `telegram_chat_id` vinculado,
la primera persona que le escribe al bot toma al usuario sembrado por
`seed.py`. Esto es lo que se reemplaza por un login real si el sistema se
vuelve multiusuario más adelante — el resto del código ya trabaja con
`user_id`, no con el chat, así que ese cambio queda contenido aquí.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


def get_default_user(session: Session) -> User:
    """El único usuario del sistema (MVP de un solo usuario) — usado por el
    panel de configuración, que no tiene un chat de Telegram del cual partir."""
    user = session.scalar(select(User).order_by(User.id).limit(1))
    if user is None:
        raise RuntimeError("No hay ningún usuario todavía. Corre primero: python -m app.services.seed")
    return user


def get_or_create_user_for_chat(session: Session, chat_id: int | str, display_name: str) -> User:
    chat_id = str(chat_id)
    user = session.scalar(select(User).where(User.telegram_chat_id == chat_id))
    if user is not None:
        return user

    unlinked = session.scalar(select(User).where(User.telegram_chat_id.is_(None)))
    if unlinked is not None:
        unlinked.telegram_chat_id = chat_id
        return unlinked

    user = User(name=display_name, telegram_chat_id=chat_id)
    session.add(user)
    session.flush()
    return user
