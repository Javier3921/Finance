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

from app.services.montos import PATRON_IMPORTE, parse_monto
from app.services.tiempo import hoy

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

_AMOUNT_RE = re.compile(rf"({PATRON_IMPORTE})\s*(?:soles|sol|s/\.?)?", re.IGNORECASE)


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
    today = today or hoy()
    normalized = _strip_accents(text.lower())

    amount_match = _AMOUNT_RE.search(normalized)
    if not amount_match:
        return None
    amount = parse_monto(amount_match.group(1))
    if amount is None:
        return None

    category, subcategory, matched_keyword = None, None, None
    for keyword, (cat, subcat) in _KEYWORDS.items():
        if re.search(rf"\b{re.escape(keyword)}\b", normalized):
            category, subcategory, matched_keyword = cat, subcat, keyword
            break

    date = _fecha_relativa(normalized, today)  # "hoy" o sin referencia explícita: el día de hoy

    return ParsedExpense(
        amount=amount,
        category=category,
        subcategory=subcategory,
        date=date,
        matched_keyword=matched_keyword,
        raw_text=text,
    )


# ---------------------------------------------------------------------------------------------------------- ingresos
# Solo frases con un verbo claro de ingreso. "Recibí" también aparece en gastos ("recibí la factura de luz"), así que
# cualquier verbo de gasto en la frase gana y se trata como gasto, igual que hasta ahora.
_INCOME_RE = re.compile(
    r"\b(cobre|me pagaron|me depositaron|me transfirieron|me yapearon|me plinearon|recibi|ingreso|ingresaron)\b"
)
_EXPENSE_VERBS_RE = re.compile(r"\b(gaste|pague|compre|gasto|pago)\b")

# palabra clave -> concepto; el primero que aparezca gana. Sin ninguna: "Otro".
INCOME_CONCEPTS = ("Sueldo", "Freelance", "Devolución", "Otro")
_INCOME_KEYWORDS: dict[str, str] = {
    "sueldo": "Sueldo",
    "salario": "Sueldo",
    "quincena": "Sueldo",
    "freelance": "Freelance",
    "proyecto": "Freelance",
    "cliente": "Freelance",
    "devolucion": "Devolución",
    "reembolso": "Devolución",
    "devolvieron": "Devolución",
}


@dataclass(frozen=True)
class ParsedIncome:
    amount: float
    concept: str
    date: dt.date
    raw_text: str


def _fecha_relativa(normalized: str, today: dt.date) -> dt.date:
    # "anteayer" primero: contiene "ayer" y, al revés, nunca se alcanzaría.
    if "anteayer" in normalized:
        return today - dt.timedelta(days=2)
    if "ayer" in normalized:
        return today - dt.timedelta(days=1)
    return today


def parse_income_text(text: str, *, today: dt.date | None = None) -> ParsedIncome | None:
    """Un ingreso si la frase tiene un verbo de ingreso, ninguno de gasto y un monto; si no, None (y el texto sigue
    su camino como gasto)."""
    today = today or hoy()
    normalized = _strip_accents(text.lower())
    if not _INCOME_RE.search(normalized) or _EXPENSE_VERBS_RE.search(normalized):
        return None
    amount_match = _AMOUNT_RE.search(normalized)
    amount = parse_monto(amount_match.group(1)) if amount_match else None
    if amount is None:
        return None
    concept = next(
        (c for k, c in _INCOME_KEYWORDS.items() if re.search(rf"\b{re.escape(k)}\b", normalized)),
        "Otro",
    )
    return ParsedIncome(amount=amount, concept=concept, date=_fecha_relativa(normalized, today), raw_text=text)
