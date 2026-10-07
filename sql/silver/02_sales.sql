-- SILVER: sales orders and deliveries
-- 1. Deduplicate re-extracted rows (keep latest extract per business key)
-- 2. Validate; route bad rows to a quarantine table with a reason
-- 3. Clean, typed, conformed output

DROP TABLE IF EXISTS tmp_so_checked;
CREATE TEMP TABLE tmp_so_checked AS
WITH deduped AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY VBELN, POSNR ORDER BY EXTRACTED_AT DESC) AS rn
    FROM bronze_sap_sales_orders
)
SELECT d.*,
       LTRIM(TRIM(d.MATNR), '0') AS material_id,
       CASE
           WHEN d.MATNR IS NULL OR TRIM(d.MATNR) = ''           THEN 'MISSING_MATERIAL'
           WHEN m.material_id IS NULL                           THEN 'UNKNOWN_MATERIAL'
           WHEN CAST(d.KWMENG AS INTEGER) <= 0                  THEN 'INVALID_QUANTITY'
           WHEN s.site_id IS NULL                               THEN 'UNKNOWN_SITE'
       END AS dq_reason
FROM deduped d
LEFT JOIN silver_material m ON m.material_id = LTRIM(TRIM(d.MATNR), '0')
LEFT JOIN silver_site s     ON s.site_id = TRIM(d.WERKS)
WHERE d.rn = 1;

DROP TABLE IF EXISTS quarantine_sales_order_line;
CREATE TABLE quarantine_sales_order_line AS
SELECT VBELN, POSNR, KUNNR, MATNR, WERKS, AUDAT, KWMENG, dq_reason, _source_file, _loaded_at
FROM tmp_so_checked
WHERE dq_reason IS NOT NULL;

DROP TABLE IF EXISTS silver_sales_order_line;
CREATE TABLE silver_sales_order_line AS
SELECT
    LTRIM(VBELN, '0')            AS order_id,
    CAST(POSNR AS INTEGER)       AS line_no,
    LTRIM(KUNNR, '0')            AS customer_id,
    material_id,
    TRIM(WERKS)                  AS site_id,
    DATE(AUDAT)                  AS order_date,
    DATE(VDATU)                  AS requested_date,
    CAST(KWMENG AS INTEGER)      AS qty_ordered
FROM tmp_so_checked
WHERE dq_reason IS NULL;

DROP TABLE IF EXISTS silver_delivery;
CREATE TABLE silver_delivery AS
WITH deduped AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY VBELN_VL ORDER BY EXTRACTED_AT DESC) AS rn
    FROM bronze_sap_deliveries
)
SELECT
    LTRIM(VBELN_VL, '0')         AS delivery_id,
    LTRIM(VGBEL, '0')            AS order_id,
    CAST(VGPOS AS INTEGER)       AS line_no,
    DATE(WADAT_IST)              AS delivery_date,
    CAST(LFIMG AS INTEGER)       AS qty_delivered
FROM deduped
WHERE rn = 1;
