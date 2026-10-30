"""Lectura del importe total de un comprobante (foto) con Gemini (visión).

Sin GEMINI_API_KEY no hace nada y devuelve None: el bot pide el monto a mano. Con la clave, la foto se envía a Google
para leerla; por eso queda desactivado por defecto y solo se activa si Javier lo configura. Cualquier fallo (cuota
agotada, saturación 503, red o respuesta inesperada) también devuelve None: un comprobante ilegible nunca bloquea el
registro.

Notas del cerebro (Gemini): la clave va en la cabecera `x-goog-api-key`, no en la URL, para que no salga en mensajes de
error; la llamada lleva tiempo máximo; los modelos 3.x gastan tokens en razonamiento interno, así que hay margen de
salida de sobra para que la respuesta no se corte.
"""
from __future__ import annotations

import base64
import contextlib
import logging

import httpx

from app.config import settings
from app.services.montos import parse_monto

logger = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
PROMPT = (
    "Es la foto de un comprobante de pago en soles (Perú). Responde solo con el importe total pagado, como número, "
    "sin texto ni símbolos, por ejemplo 35.50. Si no hay un importe total legible, responde NONE."
)
TIEMPO_MAXIMO_S = 20


def _cliente_o(cliente: httpx.Client | None):
    """Un cliente propio (y cerrado al salir) si no se pasa ninguno; el de la prueba se deja abierto."""
    return contextlib.nullcontext(cliente) if cliente is not None else httpx.Client(timeout=TIEMPO_MAXIMO_S)


def extraer_monto(imagen: bytes, *, cliente: httpx.Client | None = None) -> float | None:
    """Importe total del comprobante en soles, o None si no está configurado, no se lee o falla la llamada."""
    if not settings.gemini_api_key:
        return None
    url = f"{API_BASE}/{settings.gemini_model}:generateContent"
    cuerpo = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(imagen).decode()}},
                {"text": PROMPT},
            ],
        }],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 1024},
    }
    cabeceras = {"x-goog-api-key": settings.gemini_api_key, "content-type": "application/json"}
    try:
        with _cliente_o(cliente) as cli:
            respuesta = cli.post(url, json=cuerpo, headers=cabeceras)
        respuesta.raise_for_status()
        candidatos = respuesta.json().get("candidates") or []
        partes = candidatos[0].get("content", {}).get("parts", []) if candidatos else []
        texto = "".join(p.get("text", "") for p in partes).strip()
    except Exception:  # noqa: BLE001 - cualquier fallo aquí significa "no se pudo leer", nada más
        logger.warning("No se pudo leer el importe de la foto con Gemini", exc_info=True)
        return None
    if not texto or texto.upper().startswith("NONE"):
        return None
    return parse_monto(texto)
