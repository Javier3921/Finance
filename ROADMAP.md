# ROADMAP

Detalle de cada punto y criterios de aceptación en [`SPEC.md`](SPEC.md). Marcar al terminar cada bloque.

## Fase 1 — CI (primero, para que las demás fases ya se comprueben solas)
- [ ] Workflow de tests con Python 3.10 y 3.12
- [ ] Insignia en el README
- [ ] Comprobar verde y rojo en GitHub

## Fase 2 — Ingresos reales
- [ ] Tipo `ingreso` en el modelo y validaciones del servicio
- [ ] `/ingreso` paso a paso
- [ ] Frases de ingreso en texto libre (`nlp.py`)
- [ ] Dashboard: ingresos, balance y tasa de ahorro; quitar el placeholder de "Ingreso mensual"
- [ ] `/resumen` con ingresos y balance

## Fase 3 — /deshacer
- [ ] Servicio para borrar el último movimiento (con desglose y foto)
- [ ] Comando con confirmación por botones

## Fase 4 — Recurrentes
- [ ] Servicio: vencimiento (quincena, fin de mes, día específico) y generación sin duplicados
- [ ] Tabla `recurring_incomes` (ingresos fijos)
- [ ] Pestaña *Recurrentes* en el panel
- [ ] Job diario 08:00 + al arrancar, con botones Confirmar / Cambiar monto / Omitir
- [ ] `/pendientes`

## Fase 5 — Resumen semanal
- [ ] Servicio del resumen de la semana anterior
- [ ] Job de los lunes 08:00 y `/semana`

## Fase 6 — Despliegue
- [ ] VM: instalar `python-telegram-bot[job-queue]`, `git pull`, reiniciar
- [ ] Prueba real en Telegram de cada comando
- [ ] Actualizar README, `docs/ANALISIS_TECNICO.md` y el `CLAUDE.md` local
