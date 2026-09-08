import datetime as dt

from app.services.nlp import parse_expense_text


def test_parses_amount_and_known_category():
    parsed = parse_expense_text("Hoy gasté 35 soles en almuerzo")
    assert parsed is not None
    assert parsed.amount == 35.0
    assert parsed.category == "Comida"
    assert parsed.subcategory == "Almuerzo"
    assert parsed.is_confident


def test_parses_relative_date_ayer():
    today = dt.date(2026, 9, 5)
    parsed = parse_expense_text("ayer gasté 20 en cena", today=today)
    assert parsed.date == today - dt.timedelta(days=1)


def test_no_amount_returns_none():
    assert parse_expense_text("hola, como estas?") is None


def test_unknown_category_leaves_category_none():
    parsed = parse_expense_text("gasté 100 en no se ni que")
    assert parsed is not None
    assert parsed.amount == 100.0
    assert parsed.category is None
    assert not parsed.is_confident


def test_amount_with_comma_decimal():
    parsed = parse_expense_text("pagué 58,50 en restaurante")
    assert parsed.amount == 58.50
    assert parsed.category == "Salidas"
