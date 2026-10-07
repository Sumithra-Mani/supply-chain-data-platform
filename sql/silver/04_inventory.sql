-- SILVER: daily inventory from the WMS
-- Map WMS warehouse codes to SAP plants and convert cases (CS) to eaches (EA)
-- so stock is comparable across sites and with SAP order quantities.
-- Survivorship rule: SAP is the system of record for case size, so the SAP
-- value is used even where the WMS item master disagrees (flagged by a DQ check).

DROP TABLE IF EXISTS tmp_inv_checked;
CREATE TEMP TABLE tmp_inv_checked AS
SELECT
    i.*,
    s.site_id,
    x.material_id,
    x.sap_units_per_case,
    CASE
        WHEN s.site_id IS NULL                         THEN 'UNMAPPED_WAREHOUSE'
        WHEN x.material_id IS NULL                     THEN 'UNKNOWN_SKU'
        WHEN i.UOM NOT IN ('EA', 'CS')                 THEN 'UNKNOWN_UOM'
        WHEN CAST(i.QTY_ON_HAND AS REAL) < 0           THEN 'NEGATIVE_STOCK'
    END AS dq_reason
FROM bronze_wms_inventory_snapshots i
LEFT JOIN silver_site s           ON s.wms_wh_code = TRIM(i.WH_CODE)
LEFT JOIN silver_material_xref x  ON x.wms_sku = LTRIM(TRIM(i.SKU), '0');

DROP TABLE IF EXISTS quarantine_inventory;
CREATE TABLE quarantine_inventory AS
SELECT SNAPSHOT_DATE, WH_CODE, SKU, QTY_ON_HAND, UOM, dq_reason, _source_file, _loaded_at
FROM tmp_inv_checked WHERE dq_reason IS NOT NULL;

DROP TABLE IF EXISTS silver_inventory_daily;
CREATE TABLE silver_inventory_daily AS
SELECT
    DATE(SNAPSHOT_DATE)                                       AS snapshot_date,
    site_id,
    material_id,
    UOM                                                       AS source_uom,
    CAST(ROUND(CASE WHEN UOM = 'CS'
                    THEN CAST(QTY_ON_HAND AS REAL) * sap_units_per_case
                    ELSE CAST(QTY_ON_HAND AS REAL) END) AS INTEGER) AS qty_on_hand_ea
FROM tmp_inv_checked
WHERE dq_reason IS NULL;
