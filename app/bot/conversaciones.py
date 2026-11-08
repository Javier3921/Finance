"""Ajustes comunes de los flujos paso a paso (ConversationHandler)."""

# Un flujo abandonado a medias caduca: si no, se queda con el siguiente texto que escribas (por ejemplo, un
# "me pagaron 1500" de días después acababa leído como el monto de un /gasto viejo).
TIMEOUT_S = 15 * 60
