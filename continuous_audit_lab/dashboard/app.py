"""One-page Streamlit dashboard for the continuous audit monitor.

Shows the KRI tiles for a period, the monthly KRI trends with their thresholds, the alert list with
a drill-down to the evidence and source transactions, and the data-quality report. It reads the
finished run from DuckDB and never changes it.
"""

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from continuous_audit_lab.dashboard import data
from continuous_audit_lab.monitor.kri import KRI_NAMES

BANNER = "Demonstration on synthetic/public data; thresholds are assumptions; not a compliance tool"
DESCRIPTION = "Monthly key risk indicators, alerts and evidence from the latest audit run."
MISSING_DB_MESSAGE = "No audit database at {path}. Run `make synth && make run` first."
MISSING_LEDGER_MESSAGE = "Source ledger {path} is missing; run `make synth` or `make data` first."
NO_QUALITY_MESSAGE = "No real-data quality report loaded (synthetic run)."
NO_ALERTS_MESSAGE = "No alerts for this period."
ALERT_TABLE_COLUMNS = [
    "period",
    "rank",
    "txn_id",
    "risk_score",
    "tests_fired",
    "risk_reasons",
    "amount",
]
TILE_STYLES = {
    "green": ("#d4edda", "#155724"),
    "amber": ("#ffe8a1", "#4d3a00"),
    "red": ("#f8d7da", "#721c24"),
}
NEUTRAL_STYLE = ("#e2e3e5", "#383d41")
RAG_STATUS_TEXT = {
    "green": "GREEN",
    "amber": "AMBER",
    "red": "RED",
    "baseline": "BASELINE (sets thresholds)",
    "n/a": "N/A",
}
CHART_COLORS = ["#1f77b4", "#e0a800", "#d62728"]
TREND_COLUMNS = 3


def main() -> None:
    """Lay out the page: banner, period selector, then the four sections in order."""
    st.set_page_config(page_title="Continuous audit monitor", layout="wide")
    st.title("Continuous audit monitor")
    st.caption(DESCRIPTION)
    st.warning(BANNER)
    path = data.db_path()
    if not Path(path).exists():
        st.error(MISSING_DB_MESSAGE.format(path=path))
        return
    meta = data.load_run_meta(path)
    kris = data.load_kris(path)
    if kris.empty:
        st.error("The audit database holds no monthly KRIs; run `make run` again.")
        return
    periods = sorted(kris["period"], reverse=True)
    period = st.sidebar.selectbox("Period", periods, index=0)
    st.sidebar.caption(f"Run {meta['run_id']} · source {meta['source']}")
    render_tiles(data.latest_tiles(period, path), period)
    render_trends(kris, data.load_rag(path), data.load_thresholds(path))
    render_alerts(data.load_alerts(period, path), meta)
    render_data_quality(path)


def render_tiles(tiles: list[dict[str, Any]], period: str) -> None:
    """Show one coloured tile per KRI with its value, RAG status and amber and red limits."""
    st.subheader(f"KRIs for {period}")
    columns = st.columns(len(tiles))
    for column, tile in zip(columns, tiles):
        with column:
            st.markdown(tile_html(tile), unsafe_allow_html=True)


def tile_html(tile: dict[str, Any]) -> str:
    """Render one KRI tile as a div coloured by its RAG status."""
    background, text = TILE_STYLES.get(tile["rag"], NEUTRAL_STYLE)
    label = tile["kri"].replace("_", " ")
    status = RAG_STATUS_TEXT.get(tile["rag"], tile["rag"].upper())
    return (
        f'<div style="background:{background};color:{text};border-radius:8px;'
        f'padding:12px;min-height:150px">'
        f'<div style="font-size:0.8rem;text-transform:uppercase">{label}</div>'
        f'<div style="font-size:1.6rem;font-weight:600">{format_number(tile["value"])}</div>'
        f'<div style="font-weight:600">{status}</div>'
        f'<div style="font-size:0.75rem">amber {format_number(tile["amber"])} · '
        f"red {format_number(tile['red'])}</div>"
        "</div>"
    )


def format_number(value: float) -> str:
    """Format a KRI value with four significant digits, or n/a when it is missing."""
    if math.isnan(value):
        return "n/a"
    return f"{value:,.4g}"


def render_trends(kris: pd.DataFrame, rag: pd.DataFrame, thresholds: pd.DataFrame) -> None:
    """Draw one line per KRI over all months, with its amber and red limits as flat lines."""
    st.subheader("Monthly trend")
    limits = thresholds.set_index("kri")
    columns = st.columns(TREND_COLUMNS)
    for index, name in enumerate(KRI_NAMES):
        with columns[index % TREND_COLUMNS]:
            st.markdown(f"**{name.replace('_', ' ')}**")
            st.line_chart(trend_frame(kris, limits, name), color=CHART_COLORS)
            baseline_months = int((rag[name] == "baseline").sum())
            st.caption(
                f"Amber at {format_number(limits.at[name, 'amber'])}, red at "
                f"{format_number(limits.at[name, 'red'])}; fixed from the first "
                f"{baseline_months} months."
            )


def trend_frame(kris: pd.DataFrame, limits: pd.DataFrame, name: str) -> pd.DataFrame:
    """Build the chart series for one KRI: its monthly value and the two constant limit lines."""
    return pd.DataFrame(
        {
            name: kris[name].to_numpy(),
            "amber": limits.at[name, "amber"],
            "red": limits.at[name, "red"],
        },
        index=pd.to_datetime(kris["period"]),
    )


def render_alerts(alerts: pd.DataFrame, meta: dict[str, Any]) -> None:
    """List the period's alerts by risk score and drill into one of them on selection."""
    st.subheader("Alerts")
    if alerts.empty:
        st.info(NO_ALERTS_MESSAGE)
        return
    st.dataframe(alert_table(alerts), hide_index=True)
    position = st.selectbox(
        "Drill down into an alert",
        options=range(len(alerts)),
        format_func=lambda index: alert_label(alerts.iloc[index]),
    )
    render_drilldown(alerts.iloc[position], meta)


def alert_table(alerts: pd.DataFrame) -> pd.DataFrame:
    """Select the columns shown for alerts, with the fired tests joined into one text field."""
    table = alerts[ALERT_TABLE_COLUMNS].copy()
    table["tests_fired"] = alerts["tests_fired"].map(", ".join)
    return table


def alert_label(alert: pd.Series) -> str:
    """Name an alert in the selector by its rank, transaction id and risk score."""
    return f"Rank {int(alert['rank'])} · transaction {int(alert['txn_id'])} · score {alert['risk_score']:.3f}"


def render_drilldown(alert: pd.Series, meta: dict[str, Any]) -> None:
    """Show the alert's evidence record and the source transactions it involves."""
    st.markdown("**Evidence record**")
    st.json(evidence_record(alert))
    st.markdown("**Underlying transactions**")
    payments_path = data.payments_path_for(meta["source"])
    if not Path(payments_path).exists():
        st.warning(MISSING_LEDGER_MESSAGE.format(path=payments_path))
        return
    st.dataframe(data.transactions_for_alert(alert, payments_path), hide_index=True)


def evidence_record(alert: pd.Series) -> dict[str, Any]:
    """Collect the fields that identify an alert and bind it to its evidence hash."""
    return {
        "alert_id": str(alert["alert_id"]),
        "run_id": str(alert["run_id"]),
        "period": str(alert["period"]),
        "txn_id": int(alert["txn_id"]),
        "tests_fired": list(alert["tests_fired"]),
        "evidence_hash": str(alert["evidence_hash"]),
        "params": json.loads(alert["params_json"]),
    }


def render_data_quality(path: str) -> None:
    """Show the key and value rows of the data-quality report, or say that none is loaded."""
    st.subheader("Data quality")
    quality = data.load_data_quality(path)
    if quality.empty:
        st.info(NO_QUALITY_MESSAGE)
        return
    st.dataframe(quality, hide_index=True)


main()
