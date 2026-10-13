"""Command line entry point: download, prepare, synth, run and report."""

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from continuous_audit_lab.data import download, loader, synthetic
from continuous_audit_lab.data.schema import TXN_ID_COLUMN
from continuous_audit_lab.monitor.kri import KRI_NAMES
from continuous_audit_lab.monitor.runner import DEFAULT_ALERT_K, DEFAULT_OUT_PATH, run_months
from continuous_audit_lab.scoring.evaluate import (
    evaluate_synthetic,
    format_results_table,
    summarise,
)
from continuous_audit_lab.scoring.rules import DATE_COLUMN

SOURCES = ("synthetic", "real")
SYNTHETIC_PATH = Path("data/processed/synthetic.csv")
REAL_PATH = Path("data/processed/payments.csv")
MISSING_REAL_MESSAGE = "{path} is missing: run `make data` first"
RAG_HEADER = "| KRI | value | RAG |"
RAG_DIVIDER = "|---|---|---|"


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one sub-command per pipeline step."""
    parser = argparse.ArgumentParser(prog="continuous_audit_lab")
    sub = parser.add_subparsers(dest="command", required=True)
    dl = sub.add_parser("download", help="download public payment CSVs (idempotent)")
    dl.add_argument("--dry-run", action="store_true", help="only report total size")
    dl.add_argument("--force", action="store_true", help="allow downloads above 500 MB")
    sub.add_parser("prepare", help="map raw CSVs to the canonical schema")
    synth = sub.add_parser("synth", help="generate synthetic data with injected anomalies")
    synth.add_argument("--seed", type=int, default=42)
    run = sub.add_parser("run", help="run the monthly audit and store results in DuckDB")
    run.add_argument("--source", choices=SOURCES, default="synthetic")
    run.add_argument("--k", type=int, default=DEFAULT_ALERT_K, help="alerts per month")
    sub.add_parser("report", help="print the latest KRIs and alert summary from the run")
    return parser


TEXT_DTYPES = {
    "supplier": "string",
    "reference": "string",
    "expense_type": "string",
    "department": "string",
}


def main(argv: list[str] | None = None) -> None:
    """Dispatch the chosen sub-command."""
    args = build_parser().parse_args(argv)
    if args.command == "download":
        flags = (["--dry-run"] if args.dry_run else []) + (["--force"] if args.force else [])
        download.main(flags)
    elif args.command == "prepare":
        print(json.dumps(loader.prepare(), indent=2, default=str))
    elif args.command == "synth":
        print(synthetic.write_synthetic(seed=args.seed))
    elif args.command == "run":
        summary = run_months(load_payments(args.source), alert_k=args.k, source=args.source)
        print(json.dumps(summary, indent=2))
    elif args.command == "report":
        print(build_report(DEFAULT_OUT_PATH))


def load_payments(source: str) -> pd.DataFrame:
    """Read the payments for a run, generating the synthetic ledger first if it is missing.

    Real data must already exist from `make data`. A txn_id is assigned from row order when the
    file has none, so every row can be referenced by evidence records.
    """
    if source == "synthetic":
        if not SYNTHETIC_PATH.exists():
            synthetic.write_synthetic(SYNTHETIC_PATH)
        path = SYNTHETIC_PATH
    else:
        path = REAL_PATH
        if not path.exists():
            raise SystemExit(MISSING_REAL_MESSAGE.format(path=path))
    frame = pd.read_csv(path, parse_dates=[DATE_COLUMN], dtype=TEXT_DTYPES)
    if TXN_ID_COLUMN not in frame.columns:
        frame[TXN_ID_COLUMN] = np.arange(len(frame))
    return frame


def build_report(out_path: str) -> str:
    """Render the latest KRI values with RAG status and the alert totals from a finished run.

    Synthetic runs also get the backtest table against the injected labels. Real runs report only
    counts and the value flagged, since no labels exist to measure accuracy against.
    """
    with duckdb.connect(out_path, read_only=True) as connection:
        meta = connection.execute("SELECT * FROM run_meta").df().iloc[0]
        kris = connection.execute("SELECT * FROM kris ORDER BY period DESC LIMIT 1").df().iloc[0]
        rag = connection.execute("SELECT * FROM kri_rag ORDER BY period DESC LIMIT 1").df().iloc[0]
        alerts = (
            connection.execute(
                "SELECT count(*) AS n, coalesce(sum(amount), 0) AS value FROM alerts"
            )
            .df()
            .iloc[0]
        )
    lines = [
        f"Run {meta['run_id']} (source: {meta['source']}, k: {meta['alert_k']})",
        f"Latest KRI period: {rag['period']}",
        RAG_HEADER,
        RAG_DIVIDER,
        *(f"| {name} | {kris[name]:.4g} | {rag[name]} |" for name in KRI_NAMES),
        "",
        f"Alerts: {int(alerts['n'])}, value flagged: {alerts['value']:,.2f}",
    ]
    if meta["source"] == "synthetic":
        payments = load_payments("synthetic")
        summary = summarise(evaluate_synthetic(payments, k=int(meta["alert_k"])))
        lines += ["", format_results_table(summary)]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
