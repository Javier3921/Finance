import datetime as dt

import pytest

from app.services.montos import MONTO_MAXIMO, parse_monto
from app.services.nlp import parse_expense_text
from app.services.tiempo import LIMA, hoy


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("35", 35.0),
        ("35,5", 35.5),
        ("35.50", 35.5),
        ("S/ 35", 35.0),
        ("35 soles", 35.0),
        ("1,500", 1500.0),  # miles con coma: antes se leía como 1.5
        ("1.500", 1500.0),  # miles con punto
        ("1,500.50", 1500.5),
        ("1.200,50", 1200.5),
        ("S/ 1.200,50", 1200.5),
        ("12.345", 12345.0),  # tres cifras tras el separador: son miles, los soles tienen dos decimales
        (" 20 ", 20.0),
    ],
)
def test_lee_importes_escritos_a_mano(texto, esperado):
    assert parse_monto(texto) == esperado


@pytest.mark.parametrize("texto", ["", "0", "-5", "abc", "inf", "nan", "Infinity", "1e308", "1,5,5", "35 ... 2"])
def test_rechaza_lo_que_no_es_un_importe_valido(texto):
    assert parse_monto(texto) is None


def test_rechaza_importes_por_encima_del_tope():
    assert parse_monto(str(int(MONTO_MAXIMO) + 1)) is None
    assert parse_monto(str(int(MONTO_MAXIMO))) == float(MONTO_MAXIMO)


def test_el_parser_de_texto_usa_la_misma_lectura():
    hoy_fijo = dt.date(2026, 10, 29)
    assert parse_expense_text("gasté 1,500 en laptop", today=hoy_fijo).amount == 1500.0
    assert parse_expense_text("gasté S/ 1.200,50 en ropa", today=hoy_fijo).amount == 1200.5
    assert parse_expense_text("gasté 35 en almuerzo", today=hoy_fijo).amount == 35.0


def test_importes_malformados_en_la_frase_no_se_registran_a_medias():
    hoy_fijo = dt.date(2026, 10, 29)
    assert parse_expense_text("gasté 1,50,5 en almuerzo", today=hoy_fijo) is None
    assert parse_expense_text("gasté 1,500.500 en almuerzo", today=hoy_fijo) is None
    assert parse_expense_text("gastó S/35 en taxi", today=hoy_fijo).amount == 35.0


def test_hoy_es_la_fecha_de_lima():
    # Perú no tiene horario de verano: la zona es un desfase fijo de -5 h.
    assert LIMA.utcoffset(None) == dt.timedelta(hours=-5)
    utc = dt.timezone.utc
    # 03:00 UTC del 30 todavía es el 29 en Lima (22:00): el caso que fallaba en Streamlit Cloud.
    assert hoy(dt.datetime(2026, 10, 30, 3, 0, tzinfo=utc)) == dt.date(2026, 10, 29)
    assert hoy(dt.datetime(2026, 10, 30, 6, 0, tzinfo=utc)) == dt.date(2026, 10, 30)
