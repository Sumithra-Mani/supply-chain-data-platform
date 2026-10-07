"""
Bronze layer: land raw extracts in the warehouse exactly as received.

Every column is stored as text (no type casting, no cleaning) plus load
metadata, so bronze is a faithful, auditable copy of the source that can
always be reprocessed.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def load_bronze(raw_dir: Path, conn: sqlite3.Connection) -> dict[str, int]:
    loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counts = {}
    for csv in sorted(raw_dir.glob("*.csv")):
        table = f"bronze_{csv.stem}"
        df = pd.read_csv(csv, dtype=str)                 # keep raw text, e.g. leading zeros
        df["_source_file"] = csv.name
        df["_loaded_at"] = loaded_at
        df.to_sql(table, conn, if_exists="replace", index=False)   # full reload = idempotent
        counts[table] = len(df)
    return counts
