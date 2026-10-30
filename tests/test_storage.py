"""Fotos de comprobantes con un cliente falso de Storage: sin red ni bucket real."""
import logging
from types import SimpleNamespace

from app.services import storage


class FakeBucket:
    def __init__(self, fallar=False):
        self.llamadas = []
        self.fallar = fallar

    def create_signed_url(self, key, expires_in):
        self.llamadas.append(("firmar", key))
        if self.fallar:
            raise RuntimeError("no existe")
        return {"signedURL": f"https://x.test/{key}?token=1", "signedUrl": f"https://x.test/{key}?token=1"}

    def remove(self, keys):
        self.llamadas.append(("borrar", keys[0]))
        if self.fallar:
            raise RuntimeError("Supabase caído")
        return []


def _cliente(bucket):
    return SimpleNamespace(storage=SimpleNamespace(from_=lambda _: bucket))


def test_rutas_antiguas_de_windows_se_convierten_a_la_key_del_bucket():
    assert storage.clave("data\\receipts\\1\\1788747850_AQADaQxrGxdN8UR-.jpg") == "1/1788747850_AQADaQxrGxdN8UR-.jpg"
    assert storage.clave("data/receipts/1/x.jpg") == "1/x.jpg"
    assert storage.clave("3/1788747336_abc123.jpg") == "3/1788747336_abc123.jpg"


def test_la_foto_antigua_se_firma_con_la_key_nueva(monkeypatch):
    bucket = FakeBucket()
    monkeypatch.setattr(storage, "_client", lambda: _cliente(bucket))
    url = storage.get_signed_url("data\\receipts\\1\\x.jpg")
    assert url == "https://x.test/1/x.jpg?token=1"
    assert bucket.llamadas == [("firmar", "1/x.jpg")]


def test_foto_que_no_esta_en_el_bucket_devuelve_none_y_avisa(monkeypatch, caplog):
    monkeypatch.setattr(storage, "_client", lambda: _cliente(FakeBucket(fallar=True)))
    with caplog.at_level(logging.WARNING, logger="app.services.storage"):
        assert storage.get_signed_url("data\\receipts\\1\\x.jpg") is None
    assert "no se pudo firmar" in caplog.text.lower()


def test_borrar_con_exito_devuelve_true(monkeypatch):
    bucket = FakeBucket()
    monkeypatch.setattr(storage, "_client", lambda: _cliente(bucket))
    assert storage.delete_receipt("1/x.jpg") is True
    assert bucket.llamadas == [("borrar", "1/x.jpg")]


def test_borrar_que_falla_se_registra_y_devuelve_false(monkeypatch, caplog):
    monkeypatch.setattr(storage, "_client", lambda: _cliente(FakeBucket(fallar=True)))
    with caplog.at_level(logging.WARNING, logger="app.services.storage"):
        assert storage.delete_receipt("1/x.jpg") is False
    assert "no se pudo borrar" in caplog.text.lower()
