"""Cliente de Supabase Storage para las fotos de comprobantes.

El bot (Oracle VM) y el dashboard (Streamlit Cloud) corren en máquinas
distintas, sin disco compartido — por eso las fotos ya no se guardan en
`data/receipts/` local sino en un bucket privado de Supabase Storage.
`Transaction.receipt_path` sigue siendo un string; ahora es la key dentro
del bucket (ej. "3/1788747336_abc123.jpg") en vez de una ruta local.
"""
from __future__ import annotations

from functools import lru_cache

from app.config import settings


@lru_cache(maxsize=1)
def _client():
    from supabase import create_client

    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "Faltan SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY en el entorno — "
            "son necesarios para guardar y mostrar fotos de comprobantes."
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def upload_receipt(user_id: int, filename: str, content: bytes) -> str:
    """Sube la foto al bucket y devuelve la key para guardar en `receipt_path`."""
    key = f"{user_id}/{filename}"
    _client().storage.from_(settings.supabase_receipts_bucket).upload(
        key, content, {"content-type": "image/jpeg"}
    )
    return key


def delete_receipt(key: str) -> None:
    """Borra una foto del bucket. No falla si la key ya no existe (mismo
    comportamiento que `unlink(missing_ok=True)` con disco local)."""
    try:
        _client().storage.from_(settings.supabase_receipts_bucket).remove([key])
    except Exception:
        pass


def get_signed_url(key: str, expires_in: int = 3600) -> str | None:
    """URL temporal para mostrar la foto — el bucket es privado, sin acceso
    público directo. Devuelve None si la key ya no existe en el bucket."""
    try:
        result = _client().storage.from_(settings.supabase_receipts_bucket).create_signed_url(key, expires_in)
        return result.get("signedURL") or result.get("signed_url")
    except Exception:
        return None
