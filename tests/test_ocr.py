"""Lectura del importe de la foto con Gemini, con un transporte simulado: sin clave no hay red, la clave va en la
cabecera (no en la URL) y la respuesta se valida igual que un importe escrito a mano."""
import json
from types import SimpleNamespace

import httpx

from app.services import ocr


def _config(monkeypatch, clave):
    monkeypatch.setattr(ocr, "settings", SimpleNamespace(gemini_api_key=clave, gemini_model="gemini-3.6-flash"))


def _cliente(respuesta_texto=None, estado=200, capturadas=None):
    def manejar(request: httpx.Request) -> httpx.Response:
        if capturadas is not None:
            capturadas.append(request)
        if estado != 200:
            return httpx.Response(estado, json={"error": "fallo simulado"})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": respuesta_texto}]}}]})

    return httpx.Client(transport=httpx.MockTransport(manejar))


def test_sin_clave_no_hace_ninguna_llamada(monkeypatch):
    _config(monkeypatch, None)
    capturadas = []
    assert ocr.extraer_monto(b"jpg", cliente=_cliente("35", capturadas=capturadas)) is None
    assert capturadas == []


def test_lee_el_importe_y_manda_la_clave_en_la_cabecera(monkeypatch):
    _config(monkeypatch, "clave-de-prueba")
    capturadas = []
    assert ocr.extraer_monto(b"jpg", cliente=_cliente("35.50", capturadas=capturadas)) == 35.5

    pedido = capturadas[0]
    assert pedido.headers["x-goog-api-key"] == "clave-de-prueba"
    assert "clave-de-prueba" not in str(pedido.url)  # nunca en la URL: no aparece en trazas ni errores
    assert pedido.url.path.endswith("/models/gemini-3.6-flash:generateContent")
    partes = json.loads(pedido.content)["contents"][0]["parts"]
    assert partes[0]["inline_data"]["mime_type"] == "image/jpeg"


def test_respeta_el_formato_de_miles_de_la_respuesta(monkeypatch):
    _config(monkeypatch, "clave-de-prueba")
    assert ocr.extraer_monto(b"jpg", cliente=_cliente("1,500.00")) == 1500.0


def test_si_no_hay_importe_legible_devuelve_none(monkeypatch):
    _config(monkeypatch, "clave-de-prueba")
    assert ocr.extraer_monto(b"jpg", cliente=_cliente("NONE")) is None
    assert ocr.extraer_monto(b"jpg", cliente=_cliente("no lo sé")) is None


def test_respuesta_sin_candidatos_devuelve_none(monkeypatch):
    _config(monkeypatch, "clave-de-prueba")

    def sin_candidatos(request):
        return httpx.Response(200, json={"candidates": []})

    cliente = httpx.Client(transport=httpx.MockTransport(sin_candidatos))
    assert ocr.extraer_monto(b"jpg", cliente=cliente) is None


def test_saturacion_o_cuota_no_bloquean_el_registro(monkeypatch):
    _config(monkeypatch, "clave-de-prueba")
    assert ocr.extraer_monto(b"jpg", cliente=_cliente(estado=503)) is None  # alta demanda
    assert ocr.extraer_monto(b"jpg", cliente=_cliente(estado=429)) is None  # cuota agotada
