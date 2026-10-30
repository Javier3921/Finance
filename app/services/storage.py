"""Cliente de Supabase Storage para las fotos de comprobantes.

El bot (Oracle VM) y el dashboard (Streamlit Cloud) corren en máquinas
distintas, sin disco compartido — por eso las fotos ya no se guardan en
`data/receipts/` local sino en un bucket privado de Supabase Storage.
`Transaction.receipt_path` sigue siendo un string; ahora es la key dentro
del bucket (ej. "3/1788747336_abc123.jpg") en vez de una ruta local.

Los movimientos guardados antes de esa migración tienen rutas locales de
Windows (ej. "data\\receipts\\1\\1788747850_AQADaQxrGxdN8UR-.jpg"). `clave()` las
convierte a la key del bucket ("1/1788747850_AQADaQxrGxdN8UR-.jpg"), así que no
hace falta tocar la base de datos; la foto sí tiene que estar subida al bucket.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client():
    from supabase import create_client

    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "Faltan SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY en el entorno — "
            "son necesarios para guardar y mostrar fotos de comprobantes."
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def clave(ruta: str) -> str:
    """Key del bucket para una ruta guardada en `receipt_path`: "<usuario>/<archivo>".

    Acepta la key nueva ("1/x.jpg") y las rutas locales antiguas, con \\ o /
    ("data\\receipts\\1\\x.jpg", "data/receipts/1/x.jpg"): se queda con las dos
    últimas partes, que son siempre usuario y archivo."""
    partes = [p for p in ruta.replace("\\", "/").split("/") if p]
    return "/".join(partes[-2:]) if len(partes) >= 2 else ruta


def upload_receipt(user_id: int, filename: str, content: bytes) -> str:
    """Sube la foto al bucket y devuelve la key para guardar en `receipt_path`."""
    key = f"{user_id}/{filename}"
    _client().storage.from_(settings.supabase_receipts_bucket).upload(
        key, content, {"content-type": "image/jpeg"}
    )
    return key


def delete_receipt(ruta: str) -> bool:
    """Borra una foto del bucket. Devuelve True si se borró o ya no existía; False si falló
    (se registra en el log en vez de ignorarse, para no dejar fotos huérfanas sin saberlo)."""
    key = clave(ruta)
    try:
        _client().storage.from_(settings.supabase_receipts_bucket).remove([key])
        return True
    except Exception:
        logger.warning("No se pudo borrar el comprobante %s del bucket", key, exc_info=True)
        return False


def get_signed_url(ruta: str, expires_in: int = 3600) -> str | None:
    """URL temporal para mostrar la foto — el bucket es privado, sin acceso
    público directo. Devuelve None si la foto no está en el bucket."""
    key = clave(ruta)
    try:
        result = _client().storage.from_(settings.supabase_receipts_bucket).create_signed_url(key, expires_in)
        return result.get("signedURL") or result.get("signedUrl") or result.get("signed_url")
    except Exception:
        logger.warning("No se pudo firmar el comprobante %s (¿no está en el bucket?)", key, exc_info=True)
        return None
