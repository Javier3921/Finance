# SPEC — Ingresos reales, /deshacer, recurrentes, resumen semanal y CI

Estado: **borrador, pendiente de aprobación** (2026-11-07). Fases y avance en [`ROADMAP.md`](ROADMAP.md).

## Qué se construye

### 1. CI con GitHub Actions
- Workflow `.github/workflows/tests.yml`: en cada `push` y `pull_request` a `main` ejecuta `python -m pytest`.
- Matriz Python **3.10** (la VM del bot) y **3.12** (Streamlit Cloud), con caché de `pip`.
- Insignia de estado de los tests en el `README.md`.

### 2. Ingresos reales
- Nuevo tipo de movimiento `ingreso` (junto a `gasto`, `ahorro`, `inversion`). No usa categorías ni presupuestos de gasto.
- **`/ingreso`**, paso a paso con botón "Atrás": monto → concepto (botones *Sueldo*, *Freelance*, *Devolución*, *Otro* +
  texto libre) → fecha (hoy, ayer u otra) → confirmar.
- **Texto libre**: frases con palabras claras de ingreso (`cobré`, `me pagaron`, `recibí`, `me depositaron`, `ingreso`) se
  registran como ingreso tras la confirmación habitual. Sin esas palabras, todo sigue siendo gasto, como hoy.
- **Ingresos fijos** (sueldo): se definen en el panel y siguen el mismo mecanismo que los gastos recurrentes (punto 4).
  Un sueldo pagado en dos partes se define como dos ingresos fijos (quincena y fin de mes).
- **Dashboard**: en el rango elegido muestra *Ingresos*, *Gastos*, *Ahorro + inversión*, *Balance*
  (ingresos − gastos − ahorro − inversión) y *Tasa de ahorro* (% del ingreso que no se gastó), con la comparativa del
  periodo anterior que ya existe.
- `/resumen` del bot incluye ingresos y balance del mes.
- El ajuste "Ingreso mensual" (placeholder de 4500) deja de mostrarse en el panel; los cálculos usan solo ingresos
  registrados. Si un mes no tiene ingresos, el balance se muestra igualmente y la tasa de ahorro como "—".

### 3. `/deshacer`
- Muestra el **último movimiento registrado** (cualquier tipo y origen) con monto, categoría/concepto y fecha, y pregunta
  **[Sí, borrar] [No]**. Nada se borra sin pulsar "Sí".
- Al confirmar, borra el movimiento, su desglose y su foto del bucket de Supabase si la tenía.
- Repetir `/deshacer` va borrando hacia atrás, uno por uno, siempre con confirmación.

### 4. Gastos e ingresos recurrentes
- **Panel**: nueva pestaña *Recurrentes* para crear, editar, pausar y borrar gastos fijos (nombre, categoría,
  subcategoría, monto, método de pago) e ingresos fijos (nombre, monto).
- **Vencimiento**, elegido en el panel: *Quincena* (día 15), *Fin de mes* (último día del mes) o *Día específico*
  (1–31; si el mes es más corto, el último día del mes, p. ej. 31 → 28 de febrero).
- **Cada día a las 08:00 (Lima)** y al arrancar el bot: por cada recurrente activo cuyo vencimiento del mes ya llegó y que
  aún no tiene movimiento ese mes, el bot crea el movimiento como **pendiente** y escribe
  "Hoy vence *Netflix*: S/ 45" con **[Confirmar] [Cambiar monto] [Omitir este mes]**.
  - *Confirmar* → pasa a confirmado (gasto `fijo`) y cuenta en totales y presupuestos.
  - *Cambiar monto* → pide el monto real y confirma con ese.
  - *Omitir este mes* → queda omitido; no cuenta y no se vuelve a preguntar ese mes.
- Opción por recurrente **"Registrar sin preguntar"** (`auto_confirm`): se confirma solo y el bot solo avisa.
- Mientras esté pendiente no cuenta en ningún total. **`/pendientes`** lista los pendientes con sus botones.
- Nunca se duplica: un recurrente genera como máximo un movimiento por mes, aunque el bot se reinicie.

### 5. Resumen semanal
- **Lunes 08:00 (Lima)**, sobre la semana anterior (lunes a domingo):
  total gastado y variación frente a la semana previa, ingresos de la semana, top 3 categorías de gasto,
  presupuestos del mes en *alerta* o *excedido*, recurrentes que vencen esta semana y pendientes sin confirmar.
- **`/semana`** envía el mismo resumen a petición.
- Si la semana no tuvo movimientos, lo dice en una línea en lugar de enviar un resumen vacío.

## Qué NO se construye
- Mapa de calor / ubicación (aplazado por decisión de Javier).
- Ingresos con categorías y presupuestos propios.
- Frecuencias distintas de mensual (semanal, anual).
- Editar movimientos desde el bot (solo deshacer); la edición sigue en el panel.
- Alembic, `ruff`/`pre-commit` y despliegue automático a la VM desde CI.

## Reglas duras
- La lógica nueva vive en `app/services/` (`recurring.py`, `resumen.py`…); los handlers solo hablan con Telegram.
- Nada hardcodeado: recurrentes, montos y vencimientos son datos editables en el panel.
- **Sin cambios destructivos de esquema.** Solo tablas nuevas (`recurring_incomes`) y columnas nuevas vía
  `_NEW_COLUMNS` de `app/db.py`; no se renombra ni se borra nada. `due_day` existente se reutiliza
  (`-1` = fin de mes).
- Fechas siempre en hora de Lima (`app/services/tiempo.py`).
- Los movimientos pendientes y omitidos nunca cuentan en totales (regla actual de `list_transactions`).
- Programación con el `JobQueue` de `python-telegram-bot` (extra `[job-queue]`, añade APScheduler): un solo proceso,
  el mismo servicio `finanzas-bot`. Hay que instalar el extra en la VM.
- Nada privado en el repo (es público): ni IPs, ni claves, ni chat IDs.

## Criterios de aceptación (comprobables)
1. **CI**: un push a `main` muestra el workflow en verde en GitHub con 3.10 y 3.12; un test roto a propósito lo pone en
   rojo.
2. **Ingresos**: tests de servicio para crear ingresos, balance y tasa de ahorro (incluido mes sin ingresos);
   tests de `nlp` para las frases de ingreso y para que "gasté 50" siga siendo gasto. En Telegram, `/ingreso` registra
   y el dashboard muestra el balance correcto.
3. **/deshacer**: tests de que borra el último movimiento con su desglose, de que "No" no borra nada y de que llama a
   borrar la foto. En Telegram, deshacer un gasto con foto lo quita del dashboard y del bucket.
4. **Recurrentes**: tests del cálculo de vencimiento (15, fin de mes, 31 en febrero y años bisiestos), de no duplicar
   tras dos ejecuciones el mismo mes, de *Confirmar/Cambiar monto/Omitir* y de `auto_confirm`. En Telegram, un
   recurrente con vencimiento hoy pregunta al reiniciar el bot.
5. **Resumen**: tests del cálculo de la semana anterior (incluido el cruce de mes y de año) y del caso sin
   movimientos. En Telegram, `/semana` devuelve el resumen.
6. Todos los tests pasan en local y en CI; el bot desplegado responde tras `git pull` y reinicio.
