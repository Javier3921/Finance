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
- [ ] Servicio del resumen de la semana anterior
- [ ] Job de los lunes 08:00 y `/semana`

## Fase 6 — Despliegue
- [ ] VM: instalar `python-telegram-bot[job-queue]`, `git pull`, reiniciar
- [ ] Prueba real en Telegram de cada comando
- [ ] Actualizar README, `docs/ANALISIS_TECNICO.md` y el `CLAUDE.md` local
