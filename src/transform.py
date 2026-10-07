"""
Run the SQL transformation layers in order (silver, then gold).

Each .sql file rebuilds its tables from the layer below (DROP + CREATE),
so the whole pipeline is idempotent: re-running it gives the same result.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)
CREATE_RE = re.compile(r"CREATE\s+TABLE\s+(\w+)", re.IGNORECASE)


def run_layer(sql_dir: Path, conn: sqlite3.Connection) -> dict[str, int]:
    counts = {}
    for sql_file in sorted(sql_dir.glob("*.sql")):
        started = time.perf_counter()
        script = sql_file.read_text()
        conn.executescript(script)
        conn.commit()
        for table in CREATE_RE.findall(script):
            if table.startswith("tmp_"):
                continue
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        log.info("  %-28s %.1fs", sql_file.name, time.perf_counter() - started)
    return counts
