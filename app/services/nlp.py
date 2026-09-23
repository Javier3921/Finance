"""Parser determinístico de lenguaje natural para registrar gastos por texto.

Deliberadamente NO usa un modelo de IA: para frases simples tipo
"gasté 35 en almuerzo" una expresión regular + un diccionario de palabras
clave es suficiente, gratis, instantáneo y 100% predecible. La IA se
reserva para lo que de verdad la necesita (fase 2: OCR de comprobantes).
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass

# palabra clave -> (categoría, subcategoría | None)
_KEYWORDS: dict[str, tuple[str, str | None]] = {
    "almuerzo": ("Comida", "Almuerzo"),
    "cena": ("Comida", "Cena"),
    "desayuno": ("Comida", "Desayuno"),
    "comida": ("Comida", None),
    "cine": ("Salidas", "Cine"),
    "restaurante": ("Salidas", "Restaurante"),
    "bar": ("Salidas", "Bar"),
    "salida": ("Salidas", None),
    "celular": ("Compras", "Tecnología"),
    "laptop": ("Compras", "Tecnología"),
    "audifonos": ("Compras", "Tecnología"),
    "tecnologia": ("Compras", "Tecnología"),
    "ropa": ("Compras", "Ropa"),
    "regalo": ("Compras", "Regalos"),
    "taxi": ("Transporte", None),
    "uber": ("Transporte", None),
    "pasaje": ("Transporte", None),
    "movilidad": ("Transporte", None),
    "gimnasio": ("Salud", None),
    "streaming": ("Entretenimiento", "Streaming"),
    "netflix": ("Entretenimiento", "Streaming"),
    "concierto": ("Entretenimiento", "Concierto"),
    "videojuego": ("Entretenimiento", "Videojuego"),
}

_AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s*(?:soles|sol|s/\.?)?", re.IGNORECASE)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


@dataclass(frozen=True)
class ParsedExpense:
    amount: float
    category: str | None
    subcategory: str | None
    date: dt.date
    matched_keyword: str | None
    raw_text: str

    @property
    def is_confident(self) -> bool:
        """Suficientemente claro como para ofrecer confirmación directa
        (monto + categoría reconocidos); si falta la categoría, el bot debe
        preguntarla en vez de adivinar."""
        return self.category is not None


def parse_expense_text(text: str, *, today: dt.date | None = None) -> ParsedExpense | None:
    """Intenta extraer monto + categoría/subcategoría + fecha de una frase
    libre. Devuelve None si no se encuentra ningún monto (no hay nada que
    registrar)."""
    today = today or dt.date.today()
    normalized = _strip_accents(text.lower())

    amount_match = _AMOUNT_RE.search(normalized)
    if not amount_match:
        return None
    amount = float(amount_match.group(1).replace(",", "."))
    if amount <= 0:
        return None

    category, subcategory, matched_keyword = None, None, None
    for keyword, (cat, subcat) in _KEYWORDS.items():
        if re.search(rf"\b{re.escape(keyword)}\b", normalized):
            category, subcategory, matched_keyword = cat, subcat, keyword
            break

    # "anteayer" primero: contiene "ayer" y, al revés, nunca se alcanzaría.
    if "anteayer" in normalized:
        date = today - dt.timedelta(days=2)
    elif "ayer" in normalized:
        date = today - dt.timedelta(days=1)
    else:
        date = today  # "hoy" o sin referencia explícita: se asume el día de hoy

    return ParsedExpense(
        amount=amount,
        category=category,
        subcategory=subcategory,
        date=date,
        matched_keyword=matched_keyword,
        raw_text=text,
    )
