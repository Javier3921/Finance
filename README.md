# Finanzas_Gestor

Gestor financiero personal (gastos fijos/variables, presupuestos configurables,
ahorros e inversiones) operado principalmente vía un **bot de Telegram**, con
un **panel de configuración y dashboard** (Streamlit) para todo lo que no
tiene sentido hacer por chat.

Diseñado para un solo usuario, costo **S/0** (SQLite local + capas gratuitas),
sin IA todavía — el registro por lenguaje natural usa un parser determinístico
(palabras clave), no un modelo. Todo lo configurable (categorías,
subcategorías, presupuestos, métodos de pago, ingreso mensual, umbrales de
alerta) vive en la base de datos, nunca como constante en el código.

## Arquitectura y stack

| Pieza | Tecnología | Por qué |
|---|---|---|
| Backend / lógica de negocio | Python + SQLAlchemy 2.0 | Tipado, testeable, sin depender de Telegram ni Streamlit |
| Base de datos | SQLite local (`finanzas.db`) | Cero configuración; `DATABASE_URL` puede apuntar a Postgres/Supabase después sin tocar código |
| Bot | `python-telegram-bot` v21 (polling) | No necesita dominio ni HTTPS público |
| Panel / dashboard | Streamlit | Formularios y gráficos reales con poco código, cero costo |
| Gráficos | Altair (viene con Streamlit) | Sin dependencias nuevas |
| Lenguaje natural | Regex + diccionario de palabras clave (`app/services/nlp.py`) | Gratis, instantáneo, 100% predecible — la IA se reserva para cuando de verdad aporte (OCR, fase 2) |
| Tests | pytest sobre SQLite en memoria | No dependen de token, internet, ni la base de datos real |

## Estructura del proyecto

```
Finanzas_Gestor/
├── app/
│   ├── config.py              # lee .env (DATABASE_URL, TELEGRAM_BOT_TOKEN, etc.)
│   ├── db.py                  # motor SQLAlchemy + migraciones ligeras a mano
│   ├── models.py               # esquema completo (ver "Modelo de datos" abajo)
│   ├── services/                # lógica de negocio, sin nada de Telegram/Streamlit adentro
│   │   ├── transactions.py      # alta/consulta de movimientos
│   │   ├── budgets.py           # presupuesto vs. gastado, umbrales, prorrateo
│   │   ├── config.py             # CRUD de categorías/subcategorías/métodos/presupuestos
│   │   ├── settings.py           # ajustes editables (ingreso mensual, umbrales)
│   │   ├── nlp.py                # parser de lenguaje natural
│   │   ├── seed.py               # datos iniciales
│   │   └── users.py              # resolución de usuario (chat de Telegram -> user_id)
│   ├── bot/
│   │   ├── main.py                # arma la Application y registra los handlers
│   │   ├── formatting.py          # helpers de mensajes (montos, líneas de presupuesto)
│   │   └── handlers/
│   │       ├── expense_flow.py     # /gasto: flujo guiado paso a paso
│   │       ├── natural_language.py # registro por texto libre
│   │       ├── receipt.py          # registro con foto de comprobante
│   │       └── summary.py          # /start, /presupuesto, /resumen
│   └── webapp/
│       └── config_app.py          # panel Streamlit: dashboard + toda la configuración
├── tests/                          # 37 tests (pytest)
├── preview/dashboard.html          # mockup visual con datos de ejemplo (NO conectado a la BD real)
├── data/receipts/<user_id>/        # fotos de comprobantes (gitignored)
├── .env / .env.example
└── requirements.txt
```

## Modelo de datos (resumen)

- **User** — uno solo por ahora, pero cada tabla ya tiene `user_id` para no
  tener que rediseñar nada si el sistema se vuelve multiusuario.
- **Category / Subcategory** — se desactivan o se eliminan (nunca se pierden
  en cascada si tienen movimientos: eliminar está bloqueado en ese caso).
- **PaymentMethod**.
- **BudgetPeriod** — presupuesto de una categoría (o subcategoría)
  *"carry-forward"*: solo se guarda una fila cuando el monto cambia, y aplica
  desde ese mes en adelante hasta que se vuelva a cambiar. Los meses pasados
  nunca se reescriben.
- **Transaction** — gasto/ahorro/inversión. Campos: tipo, categoría,
  subcategoría, monto, moneda, fecha, método de pago, comercio,
  `receipt_path` (foto), origen (`manual` / `bot_texto` / `bot_foto`).
- **TransactionItem** — desglose opcional de una transacción (ej. "Compra de
  tecnología" → Celular + Audífonos).
- **RecurringExpense** — modelo listo para gastos fijos recurrentes; **el job
  que genera el borrador mensual todavía no está implementado** (ver
  "Qué falta").
- **Setting** — clave/valor por usuario: ingreso mensual, umbrales de
  aviso/alerta/excedido. Editable desde el panel, nunca hay que tocar código
  para cambiarlos.

## Funcionalidades implementadas

### Bot de Telegram
- `/start`, `/presupuesto` (estado del mes por categoría), `/resumen`.
- `/gasto` — flujo guiado con botones: tipo (fijo/variable) → categoría →
  subcategoría → monto → confirmar.
- **Registro por texto libre** ("gasté 35 en almuerzo") — reconoce monto,
  categoría/subcategoría por palabra clave y fecha relativa (hoy/ayer); si no
  reconoce la categoría, la pregunta por botones en vez de adivinar.
- **Registro con foto de comprobante**:
  - Con pie de foto (ej. "35 en almuerzo"): usa el mismo parser de texto libre.
  - Sin pie de foto: flujo 100% guiado por botones — categoría → subcategoría
    (con opción de crear una nueva ahí mismo) → monto → confirmar. Nunca
    adivina la categoría a partir de una respuesta de texto.
  - La foto se guarda en `data/receipts/<user_id>/` y queda enlazada al
    movimiento (`receipt_path`).
  - Una subcategoría creada al vuelo desde el bot **no** tiene presupuesto
    propio — no dispara ninguna alerta hasta que se le asigne uno desde el
    panel.

### Panel de configuración y dashboard (Streamlit)
- **Dashboard** — KPIs (total, fijo, variable, promedio diario), gasto por
  categoría (barras o pastel, a elección), fijo vs. variable (pastel),
  evolución en el tiempo (línea, agrupada por día/mes/año según el largo del
  rango), tabla de movimientos. Selector de rango: Hoy / Esta semana / Este
  mes / Este año / personalizado.
- **Categorías y subcategorías** — crear, renombrar, activar/desactivar,
  **eliminar** (bloqueado si tiene movimientos — ahí solo se puede
  desactivar), mover una subcategoría a otra categoría.
- **Comprobantes** — galería de fotos de gastos registrados con foto,
  filtrable por categoría, subcategoría y rango de fechas.
- **Presupuestos** — por categoría y por subcategoría (independientes entre
  sí), con historial completo y prorrateo para rangos que no son un mes
  calendario completo.
- **Métodos de pago** — crear, activar/desactivar.
- **Ajustes generales** — ingreso mensual y los tres umbrales de alerta de
  presupuesto (aviso/alerta/excedido).

Todo lo anterior queda guardado en la base de datos real: un cambio hecho en
el panel se refleja de inmediato la próxima vez que se hable con el bot, y
viceversa.

## Cómo correrlo

```bash
python -m venv .venv
.venv\Scripts\activate               # Windows
pip install -r requirements.txt

copy .env.example .env               # y completar TELEGRAM_BOT_TOKEN (crear uno con @BotFather)

python -m app.services.seed          # crea finanzas.db con categorías/métodos/presupuestos iniciales
python -m pytest                     # 37 tests, no requieren token ni internet

python -m app.bot.main               # inicia el bot (requiere TELEGRAM_BOT_TOKEN en .env)
streamlit run app/webapp/config_app.py   # abre el panel en el navegador
```

`.env` nunca se sube a git (está en `.gitignore`); `.env.example` sí, y solo
debe tener valores de ejemplo/plantilla — **nunca pegar ahí un token real**.

## Notas operativas importantes

- **El bot no tiene recarga automática.** Cualquier cambio en `app/bot/` o en
  los `services/` que usa requiere **reiniciar el proceso**
  (`python -m app.bot.main`) para que se aplique.
- **Cuidado con procesos duplicados.** Si se reinicia el bot varias veces sin
  matar bien el proceso anterior, Telegram sigue recibiendo respuestas del
  proceso viejo (con código desactualizado) de forma intermitente. En
  Windows, verificar por PID exacto, no por nombre de imagen:
  ```bash
  powershell -Command "Get-CimInstance Win32_Process -Filter \"CommandLine like '%app.bot.main%'\" | Select-Object ProcessId, CommandLine"
  ```
- **Migraciones de base de datos.** Todavía no hay Alembic. Cuando se agrega
  una columna a un modelo, `app/db.py::init_db()` la agrega sola a la base
  existente (`ALTER TABLE ... ADD COLUMN`) la próxima vez que se llame — no
  hace falta borrar `finanzas.db`, pero tampoco soporta renombrar ni borrar
  columnas.
- El panel de Streamlit y el bot son **procesos independientes** — reiniciar
  uno no afecta al otro.

## Qué falta / roadmap

### Corto plazo
- `preview/dashboard.html` sigue siendo un mockup con datos de ejemplo, no
  conectado a la base de datos real (el Dashboard de verdad ya vive en el
  panel de Streamlit).
- El registro por texto libre y por foto siempre guarda el gasto como
  **"variable"**, nunca "fijo" — no lo infiere ni lo pregunta. Si se quiere
  que categorías como "Gastos Fijos" se marquen como fijo automáticamente,
  falta esa lógica.
- **Gastos recurrentes**: el modelo (`RecurringExpense`) ya existe, pero
  falta el job que genere el borrador mensual pendiente de confirmación (o
  lo registre automáticamente, según se decida).
- `/ahorro` e `/inversion` no existen todavía como comandos del bot (el
  modelo de datos ya soporta esos tipos de movimiento).

### Fase 2
- OCR/IA para leer monto/comercio/fecha directo de la foto del comprobante
  (evaluado: Google Gemini free tier, por ser gratuito y soportar visión) —
  reemplazaría el "¿cuánto fue?" manual sin cambiar el resto del flujo.
- Alembic para migraciones versionadas de la base de datos.
- Multiusuario real (login) — el esquema ya está preparado (`user_id` en
  todas las tablas), falta la capa de autenticación.

### Fase 3
- Análisis financiero automatizado (comparaciones mes a mes, detección de
  anomalías, proyecciones de fin de mes).
- Notificaciones proactivas del bot (resumen semanal, alertas de presupuesto
  sin que el usuario tenga que preguntar).
- Migrar de SQLite a Postgres/Supabase si el uso crece (solo cambiar
  `DATABASE_URL`, el código no cambia).
