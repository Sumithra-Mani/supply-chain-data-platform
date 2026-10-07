"""
Supply chain data platform - pipeline orchestrator.

    generate (optional) -> bronze -> silver -> silver DQ -> gold -> gold DQ -> report

Usage:
    python run_pipeline.py                 # generate data and run everything
    python run_pipeline.py --skip-generate # reuse existing raw extracts
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from generate_data import Config, generate          # noqa: E402
from ingest import load_bronze                       # noqa: E402
from quality import GOLD_CHECKS, SILVER_CHECKS, DataQualityError, run_checks  # noqa: E402
from report import export_reports                    # noqa: E402
from transform import run_layer                      # noqa: E402

log = logging.getLogger("pipeline")


def run(raw_dir: Path, db_path: Path, output_dir: Path, *, skip_generate: bool = False,
        days: int = 365, products: int = 120, seed: int = 42, charts: bool = True) -> str:
    run_id = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    log.info("Run %s", run_id)

    if not skip_generate:
        log.info("[1/6] Generating source extracts -> %s", raw_dir)
        generate(Config(out_dir=raw_dir, days=days, n_products=products, seed=seed))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        log.info("[2/6] Bronze: landing raw extracts")
        for table, n in load_bronze(raw_dir, conn).items():
            log.info("  %-36s %9s rows", table, f"{n:,}")

        log.info("[3/6] Silver: clean, deduplicate, conform, quarantine")
        run_layer(ROOT / "sql" / "silver", conn)

        log.info("[4/6] Silver data quality checks")
        run_checks(conn, SILVER_CHECKS, "silver", run_id)

        log.info("[5/6] Gold: star schema and KPI marts")
        run_layer(ROOT / "sql" / "gold", conn)
        run_checks(conn, GOLD_CHECKS, "gold", run_id)

        log.info("[6/6] Exporting KPI reports -> %s", output_dir)
        export_reports(conn, output_dir, charts=charts)
    finally:
        conn.close()

    log.info("Run %s finished in %.1fs. Warehouse: %s", run_id, time.perf_counter() - started, db_path)
    return run_id


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", default="data/raw", type=Path)
    ap.add_argument("--db", default="warehouse/supply_chain.db", type=Path)
    ap.add_argument("--output", default="output", type=Path)
    ap.add_argument("--skip-generate", action="store_true")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--products", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    try:
        run(a.raw_dir, a.db, a.output, skip_generate=a.skip_generate,
            days=a.days, products=a.products, seed=a.seed)
    except DataQualityError as e:
        log.error("Pipeline stopped: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
