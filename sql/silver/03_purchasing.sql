-- SILVER: purchase orders and goods receipts

DROP TABLE IF EXISTS tmp_po_checked;
CREATE TEMP TABLE tmp_po_checked AS
WITH deduped AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY EBELN, EBELP ORDER BY EXTRACTED_AT DESC) AS rn
    FROM bronze_sap_purchase_orders
)
SELECT d.*,
       LTRIM(TRIM(d.MATNR), '0') AS material_id,
       CASE
           WHEN m.material_id IS NULL             THEN 'UNKNOWN_MATERIAL'
           WHEN s.supplier_id IS NULL             THEN 'UNKNOWN_SUPPLIER'
           WHEN CAST(d.MENGE AS INTEGER) <= 0     THEN 'INVALID_QUANTITY'
           WHEN DATE(d.EINDT) < DATE(d.BEDAT)     THEN 'PROMISED_BEFORE_ORDER'
       END AS dq_reason
FROM deduped d
LEFT JOIN silver_material m ON m.material_id = LTRIM(TRIM(d.MATNR), '0')
LEFT JOIN silver_supplier s ON s.supplier_id = LTRIM(TRIM(d.LIFNR), '0')
WHERE d.rn = 1;

DROP TABLE IF EXISTS quarantine_purchase_order_line;
CREATE TABLE quarantine_purchase_order_line AS
SELECT EBELN, EBELP, LIFNR, MATNR, WERKS, BEDAT, EINDT, MENGE, dq_reason, _source_file, _loaded_at
FROM tmp_po_checked WHERE dq_reason IS NOT NULL;

DROP TABLE IF EXISTS silver_purchase_order_line;
CREATE TABLE silver_purchase_order_line AS
SELECT
    EBELN                        AS po_id,
    CAST(EBELP AS INTEGER)       AS po_line,
    LTRIM(LIFNR, '0')            AS supplier_id,
    material_id,
    TRIM(WERKS)                  AS site_id,
    DATE(BEDAT)                  AS order_date,
    DATE(EINDT)                  AS promised_date,
    CAST(MENGE AS INTEGER)       AS qty_ordered
FROM tmp_po_checked WHERE dq_reason IS NULL;

DROP TABLE IF EXISTS tmp_gr_checked;
CREATE TEMP TABLE tmp_gr_checked AS
WITH deduped AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY MBLNR ORDER BY EXTRACTED_AT DESC) AS rn
    FROM bronze_sap_goods_receipts
)
SELECT g.*,
       CASE
           WHEN p.po_id IS NULL                    THEN 'UNKNOWN_PO'
           WHEN DATE(g.BUDAT) < p.order_date       THEN 'RECEIPT_BEFORE_ORDER'
           WHEN CAST(g.MENGE AS INTEGER) <= 0      THEN 'INVALID_QUANTITY'
       END AS dq_reason
FROM deduped g
LEFT JOIN silver_purchase_order_line p
       ON p.po_id = g.EBELN AND p.po_line = CAST(g.EBELP AS INTEGER)
WHERE g.rn = 1;

DROP TABLE IF EXISTS quarantine_goods_receipt;
CREATE TABLE quarantine_goods_receipt AS
SELECT MBLNR, EBELN, EBELP, BUDAT, MENGE, dq_reason, _source_file, _loaded_at
FROM tmp_gr_checked WHERE dq_reason IS NOT NULL;

DROP TABLE IF EXISTS silver_goods_receipt;
CREATE TABLE silver_goods_receipt AS
SELECT MBLNR AS receipt_id, EBELN AS po_id, CAST(EBELP AS INTEGER) AS po_line,
       DATE(BUDAT) AS receipt_date, CAST(MENGE AS INTEGER) AS qty_received
FROM tmp_gr_checked WHERE dq_reason IS NULL;
