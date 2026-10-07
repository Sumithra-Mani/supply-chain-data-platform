"""
Data quality checks.

Each check is a SQL query that returns the number of failing rows (0 = pass).
Severity 'error' stops the pipeline before bad data reaches users;
'warn' is logged and reported for follow-up with the data owner.
Results are stored in the dq_results table for monitoring over time.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Check:
    name: str
    dimension: str      # completeness, uniqueness, validity, consistency, timeliness, accuracy
    severity: str       # 'error' or 'warn'
    sql: str


SILVER_CHECKS = [
    # Accuracy: every distinct source record is either in silver or in quarantine
    Check("reconcile_sales_bronze_vs_silver", "accuracy", "error", """
        SELECT ABS((SELECT COUNT(*) FROM (SELECT DISTINCT VBELN, POSNR FROM bronze_sap_sales_orders))
                 - (SELECT COUNT(*) FROM silver_sales_order_line)
                 - (SELECT COUNT(*) FROM quarantine_sales_order_line))"""),
    Check("reconcile_po_bronze_vs_silver", "accuracy", "error", """
        SELECT ABS((SELECT COUNT(*) FROM (SELECT DISTINCT EBELN, EBELP FROM bronze_sap_purchase_orders))
                 - (SELECT COUNT(*) FROM silver_purchase_order_line)
                 - (SELECT COUNT(*) FROM quarantine_purchase_order_line))"""),
    Check("reconcile_inventory_bronze_vs_silver", "accuracy", "error", """
        SELECT ABS((SELECT COUNT(*) FROM bronze_wms_inventory_snapshots)
                 - (SELECT COUNT(*) FROM silver_inventory_daily)
                 - (SELECT COUNT(*) FROM quarantine_inventory))"""),
    Check("unique_sales_order_line", "uniqueness", "error", """
        SELECT COUNT(*) FROM (SELECT order_id, line_no FROM silver_sales_order_line
                              GROUP BY 1, 2 HAVING COUNT(*) > 1)"""),
    Check("unique_purchase_order_line", "uniqueness", "error", """
        SELECT COUNT(*) FROM (SELECT po_id, po_line FROM silver_purchase_order_line
                              GROUP BY 1, 2 HAVING COUNT(*) > 1)"""),
    Check("unique_inventory_snapshot", "uniqueness", "error", """
        SELECT COUNT(*) FROM (SELECT snapshot_date, site_id, material_id FROM silver_inventory_daily
                              GROUP BY 1, 2, 3 HAVING COUNT(*) > 1)"""),
    Check("not_null_sales_keys", "completeness", "error", """
        SELECT COUNT(*) FROM silver_sales_order_line
        WHERE order_id IS NULL OR material_id IS NULL OR site_id IS NULL OR order_date IS NULL"""),
    Check("no_negative_stock", "validity", "error",
          "SELECT COUNT(*) FROM silver_inventory_daily WHERE qty_on_hand_ea < 0"),
    Check("material_in_both_sap_and_wms", "consistency", "warn",
          "SELECT COUNT(*) FROM silver_material_xref WHERE wms_sku IS NULL"),
    Check("case_size_agrees_sap_vs_wms", "consistency", "warn",
          "SELECT COUNT(*) FROM silver_material_xref WHERE case_qty_matches = 0"),
    Check("sales_quarantine_rate_below_1pct", "validity", "warn", """
        SELECT CASE WHEN (SELECT COUNT(*) FROM quarantine_sales_order_line) * 1.0
                         / (SELECT COUNT(*) FROM silver_sales_order_line) > 0.01 THEN 1 ELSE 0 END"""),
    Check("inventory_feed_is_current", "timeliness", "warn", """
        SELECT CASE WHEN JULIANDAY((SELECT MAX(order_date) FROM silver_sales_order_line))
                       - JULIANDAY((SELECT MAX(snapshot_date) FROM silver_inventory_daily)) > 1
                    THEN 1 ELSE 0 END"""),
]

GOLD_CHECKS = [
    Check("unique_fact_sales_grain", "uniqueness", "error", """
        SELECT COUNT(*) FROM (SELECT order_id, line_no FROM fact_sales_order_line
                              GROUP BY 1, 2 HAVING COUNT(*) > 1)"""),
    Check("unique_fact_inventory_grain", "uniqueness", "error", """
        SELECT COUNT(*) FROM (SELECT date_sk, product_sk, site_sk FROM fact_inventory_daily
                              GROUP BY 1, 2, 3 HAVING COUNT(*) > 1)"""),
    Check("unique_dim_product_business_key", "uniqueness", "error", """
        SELECT COUNT(*) FROM (SELECT material_id FROM dim_product GROUP BY 1 HAVING COUNT(*) > 1)"""),
    Check("fact_sales_no_unknown_members", "completeness", "error", """
        SELECT COUNT(*) FROM fact_sales_order_line
        WHERE product_sk = -1 OR site_sk = -1 OR customer_sk = -1"""),
    Check("fact_sales_product_fk", "consistency", "error", """
        SELECT COUNT(*) FROM fact_sales_order_line f
        LEFT JOIN dim_product p ON p.product_sk = f.product_sk WHERE p.product_sk IS NULL"""),
    Check("fact_po_supplier_fk", "consistency", "error", """
        SELECT COUNT(*) FROM fact_purchase_order_line f
        LEFT JOIN dim_supplier s ON s.supplier_sk = f.supplier_sk WHERE s.supplier_sk IS NULL"""),
    Check("delivered_not_more_than_ordered", "validity", "error",
          "SELECT COUNT(*) FROM fact_sales_order_line WHERE qty_delivered > qty_ordered"),
    Check("dim_date_has_no_gaps", "completeness", "error", """
        SELECT ABS(COUNT(*) - (JULIANDAY(MAX(calendar_date)) - JULIANDAY(MIN(calendar_date)) + 1))
        FROM dim_date"""),
    Check("kpi_percentages_in_range", "validity", "error", """
        SELECT COUNT(*) FROM kpi_otif_monthly
        WHERE otif_pct NOT BETWEEN 0 AND 100 OR fill_rate_pct NOT BETWEEN 0 AND 100"""),
    Check("sales_reconcile_silver_vs_gold", "accuracy", "error", """
        SELECT ABS((SELECT SUM(qty_ordered) FROM silver_sales_order_line)
                 - (SELECT SUM(qty_ordered) FROM fact_sales_order_line))"""),
]


class DataQualityError(RuntimeError):
    pass


def run_checks(conn: sqlite3.Connection, checks: list[Check], layer: str, run_id: str) -> list[dict]:
    conn.execute("""CREATE TABLE IF NOT EXISTS dq_results (
        run_id TEXT, checked_at TEXT, layer TEXT, check_name TEXT, dimension TEXT,
        severity TEXT, failures INTEGER, status TEXT)""")
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = []
    for c in checks:
        failures = int(conn.execute(c.sql).fetchone()[0] or 0)
        status = "PASS" if failures == 0 else ("FAIL" if c.severity == "error" else "WARN")
        results.append(dict(run_id=run_id, checked_at=checked_at, layer=layer, check_name=c.name,
                            dimension=c.dimension, severity=c.severity, failures=failures, status=status))
        log.info("  [%s] %-38s failures=%d", status, c.name, failures)
    conn.executemany("""INSERT INTO dq_results VALUES
        (:run_id, :checked_at, :layer, :check_name, :dimension, :severity, :failures, :status)""", results)
    conn.commit()
    failed = [r["check_name"] for r in results if r["status"] == "FAIL"]
    if failed:
        raise DataQualityError(f"{layer} checks failed: {', '.join(failed)}")
    return results
