# ROADMAP

Detalle de cada punto y criterios de aceptación en [`SPEC.md`](SPEC.md). Marcar al terminar cada bloque.

## Fase 1 — CI (primero, para que las demás fases ya se comprueben solas)
- [x] Workflow de tests con Python 3.10 y 3.12
- [x] Insignia en el README
- [x] Comprobar en GitHub: verde en 3.10 y 3.12 (`a2f8c0f`). El rojo no se forzó para no romper `main`

## Fase 2 — Ingresos reales
- [x] Tipo `ingreso` en el modelo y validaciones del servicio
- [x] `/ingreso` paso a paso
- [x] Frases de ingreso en texto libre (`nlp.py`)
- [x] Dashboard: ingresos, balance y tasa de ahorro; quitar el placeholder de "Ingreso mensual" (verificado con `AppTest` sobre una base de demo; sin capturas: no había navegador)
- [x] `/resumen` con ingresos y balance

## Fase 3 — /deshacer
- [x] Servicio para borrar el último movimiento (con desglose y foto)
- [x] Comando con confirmación por botones

## Fase 4 — Recurrentes
- [x] Servicio: vencimiento (quincena, fin de mes, día específico) y generación sin duplicados
- [x] Tabla `recurring_incomes` (ingresos fijos) + RLS automático en Postgres desde `init_db()`
- [x] Pestaña *Recurrentes* en el panel (probada con `AppTest`: ver, editar, validar)
- [x] Job diario 08:00 + al arrancar, con botones Confirmar / Cambiar monto / Omitir
- [x] `/pendientes`

## Fase 5 — Resumen semanal
- [x] Servicio del resumen de la semana anterior
- [x] Job de los lunes 08:00 y `/semana`

## Revisión independiente (2026-11-07)
- [x] 10 hallazgos de un revisor independiente, todos corregidos con test (commit `9f935a0`); los graves reproducidos con
  los handlers reales y una API de Telegram falsa

## Fase 6 — Despliegue (EN CURSO, 2026-11-08)
- [x] Actualizar README, `docs/ANALISIS_TECNICO.md` y `docs/DESPLIEGUE.md`
- [ ] Javier: en el SQL Editor de Supabase, `select has_schema_privilege('finanzas_app', 'public', 'CREATE');`
  debe dar `true` (si no, la tabla nueva no se crea y el bot entra en bucle de reinicios)
- [ ] Push de los commits locales (`ac3279d`, `c2f9cb4`, `6565e78`, `9f935a0` y este) y CI en verde. Al subir,
  Streamlit aplica solo el esquema nuevo: tabla `recurring_incomes`, columna `transactions.recurring_income_id` y RLS
- [ ] VM: `git pull`, `pip install "python-telegram-bot[job-queue]>=21,<22"` en el venv, reiniciar
- [ ] Prueba real en Telegram: `/start`, `/ingreso`, `/semana`, `/deshacer`, y un gasto fijo que venza hoy
  (reiniciar el bot: el aviso llega a los ~15 s)
- [ ] Ver el dashboard en un navegador real (móvil y escritorio): solo se probó con `AppTest`
- [ ] Actualizar el `CLAUDE.md` local con el estado final
