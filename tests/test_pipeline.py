"""
End-to-end and unit tests. Runs a small simulation (60 days, 15 products)
through the whole pipeline in a temporary folder.

    python -m unittest discover -s tests -v      (or: pytest)
"""

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import run_pipeline                                  # noqa: E402
from generate_data import sap_pad                    # noqa: E402
from quality import SILVER_CHECKS, DataQualityError, run_checks  # noqa: E402


class PipelineTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.db = cls.tmp / "test.db"
        run_pipeline.run(cls.tmp / "raw", cls.db, cls.tmp / "out", days=60, products=15, seed=7, charts=False)
        cls.conn = sqlite3.connect(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp)

    def q(self, sql):
        return self.conn.execute(sql).fetchall()

    # --- unit -----------------------------------------------------------
    def test_sap_padding(self):
        self.assertEqual(sap_pad(10000123), "000000000010000123")
        self.assertEqual(len(sap_pad(1, 10)), 10)

    # --- silver ---------------------------------------------------------
    def test_no_data_quality_failures(self):
        failed = self.q("SELECT check_name FROM dq_results WHERE status = 'FAIL'")
        self.assertEqual(failed, [])

    def test_duplicate_extracts_removed(self):
        bronze_rows = self.q("SELECT COUNT(*) FROM bronze_sap_sales_orders")[0][0]
        distinct_keys = self.q("SELECT COUNT(*) FROM (SELECT DISTINCT VBELN, POSNR FROM bronze_sap_sales_orders)")[0][0]
        self.assertGreater(bronze_rows, distinct_keys, "test data should contain duplicates")
        dups = self.q("SELECT COUNT(*) FROM (SELECT order_id, line_no FROM silver_sales_order_line "
                      "GROUP BY 1, 2 HAVING COUNT(*) > 1)")[0][0]
        self.assertEqual(dups, 0)

    def test_bad_rows_quarantined_with_reason(self):
        reasons = {r[0] for r in self.q("SELECT DISTINCT dq_reason FROM quarantine_sales_order_line")}
        self.assertEqual(reasons, {"INVALID_QUANTITY", "UNKNOWN_MATERIAL", "MISSING_MATERIAL"})
        self.assertGreater(self.q("SELECT COUNT(*) FROM quarantine_goods_receipt "
                                  "WHERE dq_reason = 'RECEIPT_BEFORE_ORDER'")[0][0], 0)

    def test_leading_zeros_stripped_and_keys_join(self):
        self.assertEqual(self.q("SELECT COUNT(*) FROM silver_material WHERE material_id LIKE '0%'")[0][0], 0)
        unmatched = self.q("SELECT COUNT(*) FROM silver_material_xref WHERE wms_sku IS NULL")[0][0]
        self.assertEqual(unmatched, 0)

    def test_cases_converted_to_eaches(self):
        rows = self.q("""
            SELECT CAST(b.QTY_ON_HAND AS REAL), m.units_per_case, s.qty_on_hand_ea
            FROM bronze_wms_inventory_snapshots b
            JOIN silver_site st ON st.wms_wh_code = b.WH_CODE
            JOIN silver_material m ON m.material_id = b.SKU
            JOIN silver_inventory_daily s ON s.snapshot_date = b.SNAPSHOT_DATE
                 AND s.site_id = st.site_id AND s.material_id = m.material_id
            WHERE b.UOM = 'CS' LIMIT 200""")
        self.assertTrue(rows)
        for cases, per_case, eaches in rows:
            self.assertEqual(eaches, round(cases * per_case))

    # --- gold -----------------------------------------------------------
    def test_star_schema_keys(self):
        orphans = self.q("""SELECT COUNT(*) FROM fact_sales_order_line f
                            LEFT JOIN dim_product p ON p.product_sk = f.product_sk
                            WHERE p.product_sk IS NULL""")[0][0]
        self.assertEqual(orphans, 0)

    def test_otif_logic(self):
        bad = self.q("""SELECT COUNT(*) FROM fact_sales_order_line
                        WHERE is_otif <> (is_on_time * is_in_full)""")[0][0]
        self.assertEqual(bad, 0)
        for (pct,) in self.q("SELECT otif_pct FROM kpi_otif_monthly"):
            self.assertTrue(0 <= pct <= 100)

    def test_rerun_is_idempotent(self):
        before = self.q("SELECT COUNT(*), SUM(qty_ordered) FROM fact_sales_order_line")
        run_pipeline.run(self.tmp / "raw", self.db, self.tmp / "out", skip_generate=True, charts=False)
        after = self.q("SELECT COUNT(*), SUM(qty_ordered) FROM fact_sales_order_line")
        self.assertEqual(before, after)

    def test_failed_check_stops_pipeline(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE silver_sales_order_line (order_id, line_no)")
        conn.executemany("INSERT INTO silver_sales_order_line VALUES (?, ?)", [("1", 10), ("1", 10)])
        uniqueness = [c for c in SILVER_CHECKS if c.name == "unique_sales_order_line"]
        with self.assertRaises(DataQualityError):
            run_checks(conn, uniqueness, "silver", "test")


if __name__ == "__main__":
    unittest.main()
