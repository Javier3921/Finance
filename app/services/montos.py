"""Lectura de importes escritos a mano: "35", "35,5", "1,500", "1.200,50", "S/ 1,500.00".

Lo usan el parser de texto libre, el flujo de /gasto y el registro con foto, para que las tres vías lean igual.

Regla: los soles tienen dos decimales como mucho, así que un separador seguido de exactamente tres cifras es de miles
("1,500" y "1.500" son 1500). Un separador seguido de una o dos cifras es decimal ("35,5" son 35.5). Si hay dos
separadores, el último es el decimal y los demás son de miles ("1.200,50" son 1200.50).
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Tope de un movimiento: por encima casi seguro es un error de tecleo (un cero de más, una coma mal puesta).
MONTO_MAXIMO = Decimal("1000000")

_SIMPLE = re.compile(r"^\d+(?:[.,]\d{1,2})?$")
_MILES = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")
_MILES_Y_DECIMAL = re.compile(r"^\d{1,3}(?:[.,]\d{3})+[.,]\d{1,2}$")
_ADORNOS = re.compile(r"(?i)s/\.?|soles?|\s")

# Para buscar un importe dentro de una frase: los de miles primero, para que "1,500" no se corte en "1,50".
# Un importe no puede empezar pegado a otra cifra o separador ni terminar seguido de otro separador con cifras:
# así "1,50,5" o "1,500.500" no se leen como trozos válidos.
PATRON_IMPORTE = (
    r"(?<![\d.,])(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?![.,]?\d)"
)


def parse_monto(texto: str) -> float | None:
    """Importe en soles como float, o None si no es un número válido mayor que cero y menor o igual al tope."""
    limpio = _ADORNOS.sub("", str(texto or ""))
    if _SIMPLE.match(limpio):
        numero = limpio.replace(",", ".")
    elif _MILES.match(limpio):
        numero = re.sub(r"[.,]", "", limpio)
    elif _MILES_Y_DECIMAL.match(limpio):
        corte = max(limpio.rfind("."), limpio.rfind(","))
        numero = re.sub(r"[.,]", "", limpio[:corte]) + "." + limpio[corte + 1 :]
    else:
        return None
    try:
        valor = Decimal(numero)
    except InvalidOperation:
        return None
    if valor <= 0 or valor > MONTO_MAXIMO:
        return None
    return float(valor)
