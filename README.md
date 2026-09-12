# Finanzas_Gestor

Gestor financiero personal (gastos fijos/variables, presupuestos configurables,
ahorros e inversiones) operado principalmente vía un **bot de Telegram**, con
un **panel de configuración y dashboard** (Streamlit) para todo lo que no
tiene sentido hacer por chat.

Diseñado para un solo usuario, costo **S/0** (Postgres + Storage de Supabase,
capas gratuitas), sin IA todavía — el registro por lenguaje natural usa un
parser determinístico (palabras clave), no un modelo. Todo lo configurable
(categorías, subcategorías, presupuestos, métodos de pago, ingreso mensual,
umbrales de alerta) vive en la base de datos, nunca como constante en el
código.

**Desplegado en la nube y funcionando 24/7**, sin depender de que una PC esté
encendida, a costo **S/0** — ver el detalle de la infraestructura en
[Despliegue en la nube](#despliegue-en-la-nube-estado-actual).

## Arquitectura y stack

| Pieza | Tecnología | Por qué |
|---|---|---|
| Backend / lógica de negocio | Python + SQLAlchemy 2.0 | Tipado, testeable, sin depender de Telegram ni Streamlit |
| Base de datos | **Supabase Postgres** en producción / SQLite local (`finanzas.db`) solo para desarrollo | `DATABASE_URL` decide el motor sin tocar una línea de código — ver [Despliegue en la nube](#despliegue-en-la-nube-estado-actual) |
| Fotos de comprobantes | **Supabase Storage** (bucket privado `receipts`), vía `app/services/storage.py` | El bot y el dashboard corren en máquinas distintas sin disco compartido; antes vivían en `data/receipts/` local |
| Bot | `python-telegram-bot` v21 (polling) | No necesita dominio ni HTTPS público — corre como servicio en cualquier máquina con salida a internet |
| Hosting del bot | Oracle Cloud Free Tier (VM "Always Free", Monterrey) | Corre 24/7 como servicio `systemd` (`Restart=always`), sin depender de ninguna PC |
| Panel / dashboard | Streamlit, desplegado en **Streamlit Community Cloud** | Formularios y gráficos reales con poco código; se redeploya solo con cada `git push` a `main`, cero costo |
| Gráficos | Altair (viene con Streamlit) | Sin dependencias nuevas |
| Lenguaje natural | Regex + diccionario de palabras clave (`app/services/nlp.py`) | Gratis, instantáneo, 100% predecible — la IA se reserva para cuando de verdad aporte (OCR, fase 2) |
| Tests | pytest sobre SQLite en memoria | No dependen de token, internet, ni de Supabase |

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
│   │   ├── storage.py            # fotos de comprobantes en Supabase Storage (bucket privado)
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
├── data/receipts/<user_id>/        # legado: fotos guardadas en disco ANTES de migrar a Supabase Storage
├── credenciales/                   # NO se sube a git — credenciales reales (Supabase, GitHub PAT, etc.)
├── runtime.txt                     # fija la versión de Python para Streamlit Community Cloud
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
  - La foto se sube al bucket privado de Supabase Storage (`app/services/storage.py`)
    y queda enlazada al movimiento (`receipt_path` guarda la key dentro del bucket).
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

## Despliegue en la nube (estado actual)

Objetivo cumplido: bot + dashboard corriendo 24/7 sin depender de que una PC
esté encendida, costo **S/0**. Stack: **Supabase** (Postgres + Storage) +
**Streamlit Community Cloud** (dashboard) + **Oracle Cloud Free Tier** (VM
"Always Free" en Monterrey para el bot) + repo **GitHub privado**.

| Pieza | Estado |
|---|---|
| Repo GitHub privado | ✅ [`Javier3921/Finance`](https://github.com/Javier3921/Finance) |
| Base de datos (Supabase Postgres) | ✅ Proyecto creado, esquema migrado, datos históricos reales migrados 1:1 desde `finanzas.db` |
| Fotos de comprobantes (Supabase Storage) | ✅ Bucket privado `receipts` creado, fotos históricas migradas |
| Dashboard (Streamlit Community Cloud) | ✅ Desplegado y funcionando contra la Postgres de Supabase |
| Bot de Telegram 24/7 (Oracle Cloud) | ✅ VM `finanzas-bot` (Monterrey, `A1.Flex` 1 OCPU/2 GB), servicio `systemd` `finanzas-bot.service` con `Restart=always` |
| Verificación end-to-end completa | ✅ Un gasto registrado por Telegram aparece en el dashboard sin reiniciar nada |

Detalle técnico completo del despliegue del bot (infraestructura, comandos
de operación, incidencias encontradas y su solución): ver `CLAUDE.md` y
`INFORME_DESPLIEGUE_BOT_2026-09-10.md` en la raíz del repo.

Las credenciales reales (connection string de Supabase, `service_role` key,
token de Telegram, etc.) están en `credenciales/CREDENCIALES.md` — una
carpeta local que **nunca se sube a git** (ver `.gitignore`). Esa es la
fuente de verdad para reconfigurar cualquiera de los tres entornos (local,
VM de Oracle, Secrets de Streamlit Cloud) si hace falta.

**Cambios de código que hizo posible este stack** (relevante si se sigue
desarrollando):
- `app/db.py::_make_engine()` agrega `connect_args={"prepare_threshold":
  None}` cuando `DATABASE_URL` usa el *connection pooler* de Supabase — sin
  esto, la segunda consulta contra Postgres falla con "prepared statement
  already exists" (el pooler en modo "Transaction" no soporta prepared
  statements con nombre fijo de psycopg3).
- `runtime.txt` fija Python 3.12 para Streamlit Community Cloud — por
  defecto usa Python 3.14, donde `altair` 5.5 falla al importar
  (incompatibilidad con `TypedDict`).
- `app/webapp/config_app.py` vuelca `st.secrets` a `os.environ` al arrancar,
  porque Streamlit Cloud entrega la configuración como "Secrets" y
  `app/config.py` sigue leyendo todo con `os.getenv(...)`.
- El dashboard exige una contraseña (`DASHBOARD_PASSWORD`) antes de mostrar
  cualquier dato — sin ella, el panel se niega a arrancar. Necesario porque
  Streamlit Community Cloud publica la app en una URL accesible por
  cualquiera que la tenga.

## Cómo correrlo

### Desarrollo local (SQLite, sin depender de ninguna cuenta externa)

```bash
python -m venv .venv
.venv\Scripts\activate               # Windows
pip install -r requirements.txt

copy .env.example .env               # y completar TELEGRAM_BOT_TOKEN (crear uno con @BotFather)
# Para desarrollo local, dejar DATABASE_URL=sqlite:///./finanzas.db (valor por defecto).
# Las variables SUPABASE_* solo son necesarias si este .env va a apuntar a producción.

python -m app.services.seed          # crea finanzas.db con categorías/métodos/presupuestos iniciales
python -m pytest                     # 37 tests, no requieren token, internet ni Supabase

python -m app.bot.main               # inicia el bot (requiere TELEGRAM_BOT_TOKEN en .env)
streamlit run app/webapp/config_app.py   # abre el panel en el navegador (requiere DASHBOARD_PASSWORD en .env)
```

`.env` nunca se sube a git (está en `.gitignore`); `.env.example` sí, y solo
debe tener valores de ejemplo/plantilla — **nunca pegar ahí un token real**.

### Producción (nube)

- **Dashboard**: desplegado en Streamlit Community Cloud — cualquier
  `git push` a `main` lo redeploya solo, no requiere ninguna acción manual.
- **Bot**: corre 24/7 en una VM de Oracle Cloud como servicio `systemd`
  (`Restart=always`), apuntando al mismo `DATABASE_URL` de Supabase que usa
  el dashboard. Para actualizar el código: `cd ~/Finance && git pull && sudo
  systemctl restart finanzas-bot` en la VM. Ver `CLAUDE.md` para los
  comandos de operación y `credenciales/CREDENCIALES.md` para los valores
  exactos del `.env`.

## Notas operativas importantes

- **El bot no tiene recarga automática.** Cualquier cambio en `app/bot/` o en
  los `services/` que usa requiere **reiniciar el proceso** para que se
  aplique:
  - En local: volver a correr `python -m app.bot.main`.
  - Una vez desplegado en Oracle Cloud: `git pull && sudo systemctl restart
    finanzas-bot` en la VM.
- **Cuidado con procesos duplicados.** Si se reinicia el bot varias veces sin
  matar bien el proceso anterior, Telegram sigue recibiendo respuestas del
  proceso viejo (con código desactualizado) de forma intermitente.
  - En Windows (desarrollo local), verificar por PID exacto, no por nombre de
    imagen:
    ```bash
    powershell -Command "Get-CimInstance Win32_Process -Filter \"CommandLine like '%app.bot.main%'\" | Select-Object ProcessId, CommandLine"
    ```
  - En la VM de Oracle esto no debería pasar nunca: el bot corre como
    servicio `systemd` (`Restart=always`), que garantiza un único proceso
    administrado por PID.
- **Dashboard en Streamlit Community Cloud**: se redeploya solo con cada
  `git push` a `main` — no hace falta reiniciar nada a mano. Logs: "Manage
  app" → "Logs" en share.streamlit.io.
- **Bot en la VM de Oracle**: logs con `sudo journalctl -u finanzas-bot -f`.
- **Migraciones de base de datos.** Todavía no hay Alembic. Cuando se agrega
  una columna a un modelo, `app/db.py::init_db()` la agrega sola a la base
  existente (`ALTER TABLE ... ADD COLUMN`) la próxima vez que se llame —
  funciona igual en SQLite que en Postgres (SQL ANSI), pero tampoco soporta
  renombrar ni borrar columnas.
- El panel de Streamlit y el bot son **procesos independientes** — reiniciar
  uno no afecta al otro. Ambos comparten la misma base de datos (Supabase en
  producción), así que un cambio hecho en uno se ve de inmediato en el otro.
- **Riesgo: Supabase puede pausar el proyecto por inactividad.** El tier
  gratuito pausa la base de datos si no recibe ninguna consulta durante
  varios días seguidos — si el bot o el dashboard dejan de responder tras un
  período largo sin uso, revisar el dashboard de Supabase y reactivar el
  proyecto manualmente ahí. Mitigación futura: un ping periódico (ej.
  `JobQueue` de `python-telegram-bot` corriendo cada 24h) para mantener
  actividad constante.
- **Contraseña de Postgres**: ya rotada (2026-09-10) a una solo alfanumérica
  y propagada a los tres entornos (local, VM de Oracle, Secrets de Streamlit
  Cloud) — ver `INFORME_DESPLIEGUE_BOT_2026-09-10.md` §9.7.

## Qué falta / roadmap

### Remates del despliegue
- **Confirmar que el bot sobrevive un reinicio de la VM** (`sudo reboot` y
  verificar que `finanzas-bot` vuelve solo).
- **Restringir el ingress SSH** de la VM de `0.0.0.0/0` a la IP del usuario
  (hoy el puerto 22 está abierto a cualquier origen). Ver `CLAUDE.md`.

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
