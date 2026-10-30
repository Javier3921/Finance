"""Panel de configuración — editar categorías, subcategorías, presupuestos,
métodos de pago e ingreso mensual SIN tocar código.

Corre sobre la misma base de datos que usa el bot: un cambio hecho aquí se
ve de inmediato la próxima vez que hables con el bot.

Uso:
    streamlit run app/webapp/config_app.py

Nota para quien toque este archivo: NUNCA llames a `st.rerun()` mientras
todavía estás dentro de un bloque `with get_session():`. `st.rerun()` corta
la ejecución lanzando una excepción interna (`ScriptControlException`), y
si eso pasa antes de que el `with` termine normalmente, `get_session()`
nunca llega a hacer `commit()` — el cambio se pierde en silencio. El patrón
correcto es: terminar el `with` (que hace commit al salir sin error) y
recién DESPUÉS, ya afuera, llamar a `st.rerun()`.
"""
from __future__ import annotations

import datetime as dt
import hmac
import os
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# `streamlit run` solo agrega la carpeta de este archivo (app/webapp/) al
# sys.path, no la raíz del proyecto — sin esto, `from app.db import ...`
# falla con "ModuleNotFoundError: No module named 'app'" sin importar desde
# dónde se lance el comando.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# En Streamlit Community Cloud, las variables de entorno se configuran como
# "Secrets" (st.secrets), no como un .env real. `app.config` lee todo con
# os.getenv(...), así que las volcamos a os.environ ANTES de importar nada
# de `app` — en local esto no hace nada (st.secrets queda vacío si no existe
# secrets.toml).
try:
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except Exception:
    pass

from app.bot.formatting import STATUS_LABEL
from app.services.tiempo import hoy
from app.config import settings
from app.db import get_session, init_db
from app.services import budgets as budgets_service
from app.services import config as config_service
from app.services import settings as settings_service
from app.services import storage
from app.services import transactions as transactions_service
from app.services.users import get_default_user

st.set_page_config(page_title="Finanzas — Configuración", page_icon="⚙️", layout="wide")


def _require_password() -> None:
    """Bloquea el resto del script hasta que se ingrese la contraseña
    correcta. Falla cerrado: si DASHBOARD_PASSWORD no está configurado, la
    app se niega a mostrar nada en vez de quedar abierta por accidente."""
    if not settings.dashboard_password:
        st.error(
            "DASHBOARD_PASSWORD no está configurado (.env local o Secrets de "
            "Streamlit Cloud). El panel no puede arrancar sin una contraseña definida."
        )
        st.stop()

    if st.session_state.get("authenticated"):
        return

    st.title("🔒 Finanzas — Acceso")
    password = st.text_input("Contraseña", type="password")
    if st.button("Entrar"):
        # Comparación en tiempo constante: no revela cuántos caracteres coinciden.
        if hmac.compare_digest(password.encode(), (settings.dashboard_password or "").encode()):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta")
    st.stop()


_require_password()

init_db()

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "setiembre", "octubre", "noviembre", "diciembre",
]


def run_action(action) -> bool:
    """Ejecuta `action(session)` en su propia sesión y la cierra (con commit)
    ANTES de devolver el control — así el `st.rerun()` que haga el llamador
    ocurre siempre fuera del `with`, nunca lo interrumpe a medio commit.
    Devuelve True si no hubo errores de validación."""
    error: str | None = None
    with get_session() as session:
        try:
            action(session)
        except config_service.ConfigError as exc:
            error = str(exc)
    if error:
        st.error(error)
        return False
    return True


st.title("⚙️ Configuración de Finanzas")
st.caption(
    "Todo lo que cambies acá queda guardado en la base de datos — el bot de Telegram lo usa de inmediato, "
    "sin tocar ningún archivo de código."
)

tab_dashboard, tab_categorias, tab_comprobantes, tab_presupuestos, tab_metodos, tab_ajustes = st.tabs(
    ["Dashboard", "Categorías y subcategorías", "Comprobantes", "Presupuestos", "Métodos de pago", "Ajustes generales"]
)


def date_range_picker(key_prefix: str, default_preset: str = "Este mes") -> tuple[dt.date, dt.date]:
    """Control de rango de fechas reutilizable: día/semana/mes/año o
    personalizado. Devuelve (inicio, fin)."""
    today = hoy()
    preset = st.radio(
        "Rango de fechas", ["Hoy", "Esta semana", "Este mes", "Este año", "Personalizado"],
        horizontal=True, index=["Hoy", "Esta semana", "Este mes", "Este año", "Personalizado"].index(default_preset),
        key=f"{key_prefix}_preset",
    )
    if preset == "Hoy":
        start, end = today, today
    elif preset == "Esta semana":
        start, end = today - dt.timedelta(days=today.weekday()), today
    elif preset == "Este mes":
        start, end = today.replace(day=1), today
    elif preset == "Este año":
        start, end = dt.date(today.year, 1, 1), today
    else:
        col1, col2 = st.columns(2)
        start = col1.date_input("Desde", value=today.replace(day=1), key=f"{key_prefix}_custom_start")
        end = col2.date_input("Hasta", value=today, key=f"{key_prefix}_custom_end")
        if start > end:
            start, end = end, start
    st.caption(f"Mostrando del **{start.strftime('%d/%m/%Y')}** al **{end.strftime('%d/%m/%Y')}**")
    return start, end


# ================================================================ DASHBOARD
COLUMNAS_GASTO = ["fecha", "categoria", "subcategoria", "tipo", "monto"]


def _periodo_anterior(inicio: dt.date, fin: dt.date) -> tuple[dt.date, dt.date]:
    """El mismo número de días justo antes del rango, para comparar."""
    dias = (fin - inicio).days + 1
    return inicio - dt.timedelta(days=dias), inicio - dt.timedelta(days=1)


def _gastos(user_id: int, inicio: dt.date, fin: dt.date) -> pd.DataFrame:
    with get_session() as session:
        gastos = transactions_service.list_transactions(session, user_id, inicio, fin, kind="gasto")
        rows = [
            {
                "fecha": tx.date,
                "categoria": tx.category.name if tx.category else "Sin categoría",
                "subcategoria": tx.subcategory.name if tx.subcategory else "(Sin subcategoría)",
                "tipo": "Fijo" if tx.gasto_type == "fijo" else "Variable",
                "monto": float(tx.amount),
            }
            for tx in gastos
        ]
    return pd.DataFrame(rows, columns=COLUMNAS_GASTO)


def _variacion(actual: float, anterior: float) -> str | None:
    if anterior == 0:
        return None
    return f"{(actual - anterior) / anterior * 100:+.0f}% vs. periodo anterior"


with tab_dashboard:
    st.subheader("¿Cómo van mis gastos?")
    dash_start, dash_end = date_range_picker("dash")
    ant_start, ant_end = _periodo_anterior(dash_start, dash_end)
    with get_session() as session:
        dash_user_id = get_default_user(session).id
    df_todo = _gastos(dash_user_id, dash_start, dash_end)
    df_ant_todo = _gastos(dash_user_id, ant_start, ant_end)

    if df_todo.empty:
        st.info("No hay gastos registrados en este rango todavía.")
    else:
        f1, f2 = st.columns([2, 1])
        categorias = sorted(df_todo["categoria"].unique())
        cats_sel = f1.multiselect("Categorías", categorias, default=categorias, key="dash_cats")
        tipos_sel = f2.multiselect("Tipo de gasto", ["Fijo", "Variable"], default=["Fijo", "Variable"], key="dash_tipos")

        def _filtrar(d: pd.DataFrame) -> pd.DataFrame:
            return d[d["categoria"].isin(cats_sel) & d["tipo"].isin(tipos_sel)]

        df = _filtrar(df_todo)
        df_ant = _filtrar(df_ant_todo)

        if df.empty:
            st.warning("Ningún gasto cumple los filtros. Amplía las categorías o el tipo de gasto.")
        else:
            total, total_ant = df["monto"].sum(), df_ant["monto"].sum()
            fijo = df.loc[df["tipo"] == "Fijo", "monto"].sum()
            variable = df.loc[df["tipo"] == "Variable", "monto"].sum()
            n_dias = (dash_end - dash_start).days + 1
            promedio, promedio_ant = total / n_dias, total_ant / ((ant_end - ant_start).days + 1)

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Total gastado", f"S/ {total:,.0f}", delta=_variacion(total, total_ant), delta_color="inverse")
            k2.metric("Gasto fijo", f"S/ {fijo:,.0f}")
            k3.metric("Gasto variable", f"S/ {variable:,.0f}")
            k4.metric("Promedio diario", f"S/ {promedio:,.0f}", delta=_variacion(promedio, promedio_ant), delta_color="inverse")

            vista_resumen, vista_movimientos, vista_presupuesto = st.tabs(["Resumen", "Movimientos", "Presupuesto del rango"])

            with vista_resumen:
                col_left, col_right = st.columns([1.3, 1])
                with col_left:
                    st.write("**Gasto por categoría**")
                    chart_type = st.segmented_control(
                        "Tipo de gráfico", ["Barras", "Pastel"], default="Barras", key="dash_cat_chart_type",
                        label_visibility="collapsed",
                    )
                    by_cat = df.groupby("categoria", as_index=False)["monto"].sum().sort_values("monto", ascending=False)
                    if chart_type == "Pastel":
                        cat_chart = (
                            alt.Chart(by_cat)
                            .mark_arc(innerRadius=60)
                            .encode(
                                theta=alt.Theta("monto:Q"),
                                color=alt.Color("categoria:N", title="Categoría"),
                                tooltip=[alt.Tooltip("categoria:N", title="Categoría"), alt.Tooltip("monto:Q", title="Monto", format=",.0f")],
                            )
                        )
                    else:
                        cat_chart = (
                            alt.Chart(by_cat)
                            .mark_bar()
                            .encode(
                                x=alt.X("monto:Q", title="Gastado (S/)"),
                                y=alt.Y("categoria:N", sort="-x", title=""),
                                tooltip=[alt.Tooltip("categoria:N", title="Categoría"), alt.Tooltip("monto:Q", title="Monto", format=",.0f")],
                            )
                        )
                    st.altair_chart(cat_chart, width="stretch")

                with col_right:
                    st.write("**Fijo vs. variable**")
                    by_type = df.groupby("tipo", as_index=False)["monto"].sum()
                    type_chart = (
                        alt.Chart(by_type)
                        .mark_arc(innerRadius=60)
                        .encode(
                            theta=alt.Theta("monto:Q"),
                            color=alt.Color("tipo:N", title="Tipo", scale=alt.Scale(domain=["Fijo", "Variable"])),
                            tooltip=[alt.Tooltip("tipo:N", title="Tipo"), alt.Tooltip("monto:Q", title="Monto", format=",.0f")],
                        )
                    )
                    st.altair_chart(type_chart, width="stretch")

                st.write("**Evolución en el tiempo**")
                span_days = (dash_end - dash_start).days
                fechas = pd.to_datetime(df["fecha"])
                if span_days <= 31:
                    bucket, bucket_title = fechas.dt.normalize(), "Día"
                elif span_days <= 731:
                    bucket, bucket_title = fechas.dt.to_period("M").dt.to_timestamp(), "Mes"
                else:
                    bucket, bucket_title = fechas.dt.to_period("Y").dt.to_timestamp(), "Año"
                evolucion = df.assign(bucket=bucket).groupby("bucket", as_index=False)["monto"].sum().sort_values("bucket")
                line_chart = (
                    alt.Chart(evolucion)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("bucket:T", title=bucket_title),
                        y=alt.Y("monto:Q", title="Gastado (S/)"),
                        tooltip=[alt.Tooltip("bucket:T", title=bucket_title), alt.Tooltip("monto:Q", title="Monto", format=",.0f")],
                    )
                )
                st.altair_chart(line_chart, width="stretch")

                st.write("**Los 5 gastos más grandes del rango**")
                top = df.nlargest(5, "monto")[COLUMNAS_GASTO]
                st.dataframe(
                    top,
                    column_config={
                        "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                        "monto": st.column_config.NumberColumn("Monto", format="S/ %.2f"),
                    },
                    width="stretch", hide_index=True,
                )

            with vista_movimientos:
                buscar = st.text_input("Buscar por categoría o subcategoría", key="dash_buscar", placeholder="Ej. almuerzo")
                tabla = df[COLUMNAS_GASTO].sort_values("fecha", ascending=False)
                if buscar.strip():
                    texto = buscar.strip().lower()
                    tabla = tabla[
                        tabla["categoria"].str.lower().str.contains(texto, regex=False)
                        | tabla["subcategoria"].str.lower().str.contains(texto, regex=False)
                    ]
                st.caption(f"{len(tabla)} movimiento(s) · total S/ {tabla['monto'].sum():,.2f}")
                st.dataframe(
                    tabla,
                    column_config={
                        "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                        "monto": st.column_config.NumberColumn("Monto", format="S/ %.2f"),
                    },
                    width="stretch", hide_index=True,
                )
                st.download_button(
                    "Descargar CSV", data=tabla.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"gastos_{dash_start:%Y%m%d}_{dash_end:%Y%m%d}.csv", mime="text/csv",
                )

            with vista_presupuesto:
                st.caption(
                    "Compara con el presupuesto mensual completo de cada mes del rango. En rangos cortos (hoy, esta semana) "
                    "el porcentaje sale bajo: el gasto es de unos días y el presupuesto, de un mes entero."
                )
                with get_session() as session:
                    estados = budgets_service.category_budget_statuses(session, dash_user_id, dash_start, dash_end)
                con_presupuesto = [e for e in estados if e.budget > 0]
                sin_presupuesto = [e for e in estados if e.budget == 0 and e.spent > 0]
                if not con_presupuesto and not sin_presupuesto:
                    st.info("Ninguna categoría tiene presupuesto ni gastos en este rango.")
                for e in con_presupuesto:
                    avance = min(float(e.pct_used) / 100, 1.0)
                    st.progress(
                        avance,
                        text=f"{e.category_name}: S/ {float(e.spent):,.0f} de S/ {float(e.budget):,.0f} "
                             f"({int(e.pct_used)}%) · {STATUS_LABEL[e.status]}",
                    )
                for e in sin_presupuesto:
                    st.caption(f"{e.category_name}: S/ {float(e.spent):,.0f} gastados, sin presupuesto asignado")

# ============================================================== CATEGORÍAS
with tab_categorias:
    with get_session() as session:
        user = get_default_user(session)
        categories = config_service.list_categories(session, user.id, include_inactive=True)
        rows = [
            (c.id, c.name, c.is_active, config_service.count_transactions_for_category(session, c.id))
            for c in categories
        ]

    st.subheader("Categorías")
    if not rows:
        st.info("Todavía no hay categorías. Crea la primera abajo.")
    for cat_id, cat_name, cat_active, n_tx in rows:
        with st.container(border=True):
            cols = st.columns([3, 1, 2, 1, 1])
            new_name = cols[0].text_input("Nombre", value=cat_name, key=f"cat_name_{cat_id}", label_visibility="collapsed")
            new_active = cols[1].checkbox("Activa", value=cat_active, key=f"cat_active_{cat_id}")
            cols[2].caption(f"{n_tx} movimiento(s) registrados con esta categoría")

            if cols[3].button("Guardar", key=f"cat_save_{cat_id}"):
                ok = run_action(lambda s: (
                    config_service.rename_category(s, cat_id, new_name),
                    config_service.set_category_active(s, cat_id, new_active),
                ))
                if ok:
                    st.success("Guardado.")
                    st.rerun()

            if cols[4].button("🗑️ Eliminar", key=f"cat_delete_{cat_id}"):
                ok = run_action(lambda s: config_service.delete_category(s, cat_id))
                if ok:
                    st.success(f"'{cat_name}' eliminada.")
                    st.rerun()

            if not new_active and n_tx > 0:
                st.caption(
                    f"⚠️ Esta categoría tiene {n_tx} movimiento(s) — no se puede eliminar (por eso 'Eliminar' "
                    "dará error), pero al desactivarla deja de aparecer para registrar gastos nuevos sin "
                    "tocar el histórico."
                )

    with st.form("new_category_form", clear_on_submit=True):
        st.write("Nueva categoría")
        name = st.text_input("Nombre", label_visibility="collapsed", placeholder="ej. Mascotas")
        if st.form_submit_button("Crear categoría") and name.strip():
            ok = run_action(lambda s: config_service.create_category(s, get_default_user(s).id, name))
            if ok:
                st.success(f"Categoría '{name}' creada.")
                st.rerun()

    st.divider()
    st.subheader("Subcategorías")
    with get_session() as session:
        user = get_default_user(session)
        active_categories = config_service.list_categories(session, user.id)
        active_cat_options = {c.id: c.name for c in active_categories}

    if not active_cat_options:
        st.info("Crea al menos una categoría activa primero.")
    else:
        selected_cat_id = st.selectbox(
            "Ver subcategorías de:", options=list(active_cat_options.keys()),
            format_func=lambda cid: active_cat_options[cid], key="subcat_parent_select",
        )
        with get_session() as session:
            subcats = config_service.list_subcategories(session, selected_cat_id, include_inactive=True)
            sub_rows = [
                (s.id, s.name, s.is_active, s.category_id, config_service.count_transactions_for_subcategory(session, s.id))
                for s in subcats
            ]

        for sub_id, sub_name, sub_active, sub_cat_id, sub_n_tx in sub_rows:
            with st.container(border=True):
                cols = st.columns([3, 1, 3, 1, 1])
                new_sub_name = cols[0].text_input("Nombre", value=sub_name, key=f"sub_name_{sub_id}", label_visibility="collapsed")
                new_sub_active = cols[1].checkbox("Activa", value=sub_active, key=f"sub_active_{sub_id}")
                move_to = cols[2].selectbox(
                    "Mover a categoría", options=list(active_cat_options.keys()),
                    format_func=lambda cid: active_cat_options[cid],
                    index=list(active_cat_options.keys()).index(sub_cat_id) if sub_cat_id in active_cat_options else 0,
                    key=f"sub_move_{sub_id}", label_visibility="collapsed",
                )

                if cols[3].button("Guardar", key=f"sub_save_{sub_id}"):
                    def _save_sub(s, sub_id=sub_id, new_sub_name=new_sub_name, new_sub_active=new_sub_active, move_to=move_to, sub_cat_id=sub_cat_id):
                        config_service.rename_subcategory(s, sub_id, new_sub_name)
                        config_service.set_subcategory_active(s, sub_id, new_sub_active)
                        if move_to != sub_cat_id:
                            config_service.move_subcategory(s, sub_id, move_to)
                    ok = run_action(_save_sub)
                    if ok:
                        st.success("Guardado.")
                        st.rerun()

                if cols[4].button("🗑️ Eliminar", key=f"sub_delete_{sub_id}"):
                    ok = run_action(lambda s, sub_id=sub_id: config_service.delete_subcategory(s, sub_id))
                    if ok:
                        st.success(f"'{sub_name}' eliminada.")
                        st.rerun()

                if sub_n_tx > 0:
                    st.caption(f"⚠️ {sub_n_tx} movimiento(s) registrados — no se puede eliminar, solo desactivar.")

        with st.form("new_subcategory_form", clear_on_submit=True):
            st.write(f"Nueva subcategoría en '{active_cat_options[selected_cat_id]}'")
            sub_name_input = st.text_input("Nombre", label_visibility="collapsed", placeholder="ej. Veterinario")
            if st.form_submit_button("Agregar subcategoría") and sub_name_input.strip():
                ok = run_action(lambda s: config_service.create_subcategory(s, selected_cat_id, sub_name_input))
                if ok:
                    st.success("Creada.")
                    st.rerun()

# ============================================================= COMPROBANTES
with tab_comprobantes:
    st.subheader("Fotos de comprobantes")
    st.caption("Todos los gastos que registraste enviándole una foto al bot de Telegram.")

    with get_session() as session:
        user = get_default_user(session)
        all_cats = config_service.list_categories(session, user.id, include_inactive=True)
        cat_filter_options: dict[int | None, str] = {None: "Todas las categorías"}
        cat_filter_options.update({c.id: c.name for c in all_cats})

    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    receipt_cat_id = col_f1.selectbox(
        "Categoría", options=list(cat_filter_options.keys()), format_func=lambda cid: cat_filter_options[cid],
        key="receipt_cat_filter",
    )

    sub_filter_options: dict[int | None, str] = {None: "Todas las subcategorías"}
    if receipt_cat_id is not None:
        with get_session() as session:
            subs = config_service.list_subcategories(session, receipt_cat_id, include_inactive=True)
            sub_filter_options.update({s.id: s.name for s in subs})
    receipt_sub_id = col_f2.selectbox(
        "Subcategoría", options=list(sub_filter_options.keys()), format_func=lambda sid: sub_filter_options[sid],
        key=f"receipt_sub_filter_{receipt_cat_id}",
        disabled=receipt_cat_id is None,
    )

    receipt_start = col_f3.date_input("Desde", value=dt.date(2020, 1, 1), key="receipt_start")
    receipt_end = col_f4.date_input("Hasta", value=hoy(), key="receipt_end")

    with get_session() as session:
        user = get_default_user(session)
        receipts = transactions_service.list_transactions(
            session, user.id, receipt_start, receipt_end,
            category_id=receipt_cat_id, subcategory_id=receipt_sub_id, with_receipt=True,
        )
        # armamos toda la info que necesitamos mostrar mientras la sesión sigue abierta —
        # `tx.category`/`tx.subcategory` son relaciones que se cargan perezosamente y
        # dejan de estar disponibles apenas se cierra la sesión.
        receipt_rows = [
            {
                "date": tx.date,
                "category": tx.category.name if tx.category else "—",
                "subcategory": tx.subcategory.name if tx.subcategory else None,
                "amount": float(tx.amount),
                "method": tx.payment_method.name if tx.payment_method else None,
                "merchant": tx.merchant,
                "receipt_path": tx.receipt_path,
            }
            for tx in receipts
        ]

    if not receipt_rows:
        st.info("Todavía no hay gastos con foto de comprobante en este filtro.")
    else:
        st.write(f"{len(receipt_rows)} comprobante(s)")
        grid_cols = st.columns(3)
        for i, r in enumerate(receipt_rows):
            with grid_cols[i % 3]:
                with st.container(border=True):
                    signed_url = storage.get_signed_url(r["receipt_path"]) if r["receipt_path"] else None
                    if signed_url:
                        st.image(signed_url, width="stretch")
                    else:
                        st.warning("No se pudo obtener la foto del comprobante desde Supabase Storage.")
                    destino = f"{r['category']} → {r['subcategory']}" if r["subcategory"] else r["category"]
                    st.markdown(f"**{destino}**")
                    st.write(f"S/ {r['amount']:,.0f} · {r['date'].strftime('%d/%m/%Y')}")
                    detail_bits = [b for b in (r["merchant"], r["method"]) if b]
                    if detail_bits:
                        st.caption(" · ".join(detail_bits))

# ============================================================= PRESUPUESTOS
with tab_presupuestos:
    st.subheader("Presupuesto mensual por categoría")
    st.caption(
        "El monto que fijes aplica desde el mes elegido EN ADELANTE, hasta que lo cambies de nuevo — "
        "los meses anteriores conservan el presupuesto que tenían en su momento."
    )

    with get_session() as session:
        user = get_default_user(session)
        cats = config_service.list_categories(session, user.id)
        cat_options = {c.id: c.name for c in cats}

    if not cat_options:
        st.info("Crea al menos una categoría primero, en la pestaña anterior.")
    else:
        col_a, col_b = st.columns(2)
        budget_cat_id = col_a.selectbox("Categoría", options=list(cat_options.keys()), format_func=lambda cid: cat_options[cid])

        with get_session() as session:
            subcats = config_service.list_subcategories(session, budget_cat_id)
            sub_options: dict[int | None, str] = {None: "(Toda la categoría)"}
            sub_options.update({s.id: s.name for s in subcats})
        budget_sub_id = col_b.selectbox(
            "Subcategoría (opcional)", options=list(sub_options.keys()), format_func=lambda sid: sub_options[sid],
            key=f"budget_sub_select_{budget_cat_id}",
        )
        target_label = (
            f"{cat_options[budget_cat_id]} → {sub_options[budget_sub_id]}" if budget_sub_id else cat_options[budget_cat_id]
        )

        col_c, col_d = st.columns(2)
        today = hoy()
        budget_year = col_c.number_input("Año", min_value=2020, max_value=2100, value=today.year, step=1)
        budget_month = col_d.selectbox("Mes (aplica desde acá)", options=list(range(1, 13)), format_func=lambda m: MESES[m - 1].capitalize(), index=today.month - 1)

        with get_session() as session:
            user = get_default_user(session)
            current = config_service.get_budget_history(session, budget_cat_id, subcategory_id=budget_sub_id)
            vigente = next((b.amount for b in current if (b.year, b.month) <= (budget_year, budget_month)), None)
            month_start = today.replace(day=1)
            spent_this_month = budgets_service.amount_spent(session, user.id, budget_cat_id, month_start, today, subcategory_id=budget_sub_id)

        col_metric_1, col_metric_2 = st.columns(2)
        col_metric_1.metric("Presupuesto vigente antes de este cambio", f"S/ {vigente:,.0f}" if vigente is not None else "Sin definir")
        col_metric_2.metric("Gastado este mes (a la fecha)", f"S/ {spent_this_month:,.0f}")

        new_amount = st.number_input("Nuevo presupuesto mensual (S/)", min_value=0.0, step=10.0, value=float(vigente or 0))
        if st.button("Guardar presupuesto"):
            ok = run_action(lambda s: config_service.set_budget(
                s, budget_cat_id, int(budget_year), int(budget_month), new_amount, subcategory_id=budget_sub_id
            ))
            if ok:
                st.success(f"Presupuesto de '{target_label}' fijado en S/ {new_amount:,.0f} desde {MESES[budget_month-1]} {budget_year}.")
                st.rerun()

        st.divider()
        st.write(f"Historial de presupuestos — {target_label}")
        with get_session() as session:
            history = config_service.get_budget_history(session, budget_cat_id, subcategory_id=budget_sub_id)
        if history:
            st.table(
                [{"Desde": f"{MESES[b.month - 1].capitalize()} {b.year}", "Monto": f"S/ {b.amount:,.0f}"} for b in history]
            )
        else:
            st.caption(f"Sin presupuestos definidos todavía para '{target_label}'.")

        if subcats:
            st.divider()
            st.write(f"Subcategorías de '{cat_options[budget_cat_id]}' con presupuesto propio")
            with get_session() as session:
                user = get_default_user(session)
                sub_statuses = budgets_service.subcategory_budget_statuses(session, user.id, budget_cat_id, month_start, today)
            if sub_statuses:
                st.table(
                    [
                        {
                            "Subcategoría": s.subcategory_name,
                            "Gastado este mes": f"S/ {s.spent:,.0f}",
                            "Presupuesto": f"S/ {s.budget:,.0f}",
                            "% usado": f"{int(s.pct_used)}%",
                        }
                        for s in sub_statuses
                    ]
                )
            else:
                st.caption("Ninguna subcategoría tiene un presupuesto propio todavía — elige una arriba para asignarle uno.")

# ============================================================ MÉTODOS DE PAGO
with tab_metodos:
    st.subheader("Métodos de pago")
    with get_session() as session:
        user = get_default_user(session)
        methods = config_service.list_payment_methods(session, user.id, include_inactive=True)
        method_rows = [(m.id, m.name, m.is_active) for m in methods]

    for method_id, method_name, method_active in method_rows:
        with st.container(border=True):
            cols = st.columns([3, 1, 1])
            cols[0].write(method_name)
            new_active = cols[1].checkbox("Activo", value=method_active, key=f"method_active_{method_id}")
            if cols[2].button("Guardar", key=f"method_save_{method_id}"):
                ok = run_action(lambda s: config_service.set_payment_method_active(s, method_id, new_active))
                if ok:
                    st.success("Guardado.")
                    st.rerun()

    with st.form("new_method_form", clear_on_submit=True):
        st.write("Nuevo método de pago")
        method_name_input = st.text_input("Nombre", label_visibility="collapsed", placeholder="ej. Billetera digital")
        if st.form_submit_button("Agregar método") and method_name_input.strip():
            ok = run_action(lambda s: config_service.create_payment_method(s, get_default_user(s).id, method_name_input))
            if ok:
                st.success("Creado.")
                st.rerun()

# ============================================================= AJUSTES GENERALES
with tab_ajustes:
    st.subheader("Ajustes generales")
    with get_session() as session:
        user = get_default_user(session)
        current_settings = settings_service.all_settings(session, user.id)

    with st.form("settings_form"):
        income = st.number_input(
            "Ingreso mensual (S/)", min_value=0.0, step=50.0, value=float(current_settings["monthly_income"]),
            help="Se usa para calcular cuánto queda 'disponible' en los resúmenes.",
        )
        st.write("Umbrales de alerta de presupuesto (% usado)")
        c1, c2, c3 = st.columns(3)
        aviso = c1.number_input("Aviso desde", min_value=0, max_value=200, value=int(float(current_settings["budget_aviso_pct"])))
        alerta = c2.number_input("Alerta desde", min_value=0, max_value=200, value=int(float(current_settings["budget_alerta_pct"])))
        excedido = c3.number_input("Excedido desde", min_value=0, max_value=300, value=int(float(current_settings["budget_excedido_pct"])))

        if st.form_submit_button("Guardar ajustes"):
            if not (aviso <= alerta <= excedido):
                st.error("Los umbrales deben ir en orden: aviso ≤ alerta ≤ excedido.")
            else:
                def _save_settings(s):
                    u = get_default_user(s)
                    settings_service.set_setting(s, u.id, "monthly_income", str(income))
                    settings_service.set_setting(s, u.id, "budget_aviso_pct", str(aviso))
                    settings_service.set_setting(s, u.id, "budget_alerta_pct", str(alerta))
                    settings_service.set_setting(s, u.id, "budget_excedido_pct", str(excedido))
                ok = run_action(_save_settings)
                if ok:
                    st.success("Ajustes guardados.")
                    st.rerun()
