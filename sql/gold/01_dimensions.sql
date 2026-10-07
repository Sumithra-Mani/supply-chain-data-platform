-- GOLD: conformed dimensions (Kimball star schema)
-- Surrogate keys are generated in the warehouse; each dimension has an
-- 'Unknown' member (-1) so a fact row never loses its key.

DROP TABLE IF EXISTS dim_date;
CREATE TABLE dim_date AS
WITH RECURSIVE bounds AS (
    SELECT MIN(d) AS start_date, MAX(d) AS end_date FROM (
        SELECT order_date AS d FROM silver_sales_order_line
        UNION ALL SELECT requested_date FROM silver_sales_order_line
        UNION ALL SELECT delivery_date FROM silver_delivery
        UNION ALL SELECT order_date FROM silver_purchase_order_line
        UNION ALL SELECT promised_date FROM silver_purchase_order_line
        UNION ALL SELECT receipt_date FROM silver_goods_receipt
        UNION ALL SELECT snapshot_date FROM silver_inventory_daily
    )
),
days(d) AS (
    SELECT start_date FROM bounds
    UNION ALL
    SELECT DATE(d, '+1 day') FROM days, bounds WHERE d < bounds.end_date
)
SELECT
    CAST(STRFTIME('%Y%m%d', d) AS INTEGER)               AS date_sk,
    d                                                    AS calendar_date,
    CAST(STRFTIME('%Y', d) AS INTEGER)                   AS year,
    CAST(STRFTIME('%m', d) AS INTEGER)                   AS month_num,
    STRFTIME('%Y-%m', d)                                 AS year_month,
    DATE(d, 'start of month')                            AS month_start,
    (CAST(STRFTIME('%m', d) AS INTEGER) + 2) / 3         AS quarter,
    DATE(d, 'weekday 1', '-7 days')                      AS week_start_monday,
    CASE CAST(STRFTIME('%w', d) AS INTEGER)
        WHEN 0 THEN 'Sun' WHEN 1 THEN 'Mon' WHEN 2 THEN 'Tue' WHEN 3 THEN 'Wed'
        WHEN 4 THEN 'Thu' WHEN 5 THEN 'Fri' ELSE 'Sat' END AS day_name,
    CASE WHEN STRFTIME('%w', d) IN ('0', '6') THEN 1 ELSE 0 END AS is_weekend
FROM days;

DROP TABLE IF EXISTS dim_product;
CREATE TABLE dim_product AS
SELECT ROW_NUMBER() OVER (ORDER BY material_id) AS product_sk,
       material_id, sap_matnr, material_name, material_type, category,
       base_uom, units_per_case, standard_cost, shelf_life_days, primary_supplier_id
FROM silver_material
UNION ALL
SELECT -1, 'UNKNOWN', NULL, 'Unknown product', NULL, 'UNKNOWN', NULL, NULL, NULL, NULL, NULL;

DROP TABLE IF EXISTS dim_site;
CREATE TABLE dim_site AS
SELECT ROW_NUMBER() OVER (ORDER BY site_id) AS site_sk, site_id, wms_wh_code, site_name, country
FROM silver_site
UNION ALL SELECT -1, 'UNKNOWN', NULL, 'Unknown site', NULL;

DROP TABLE IF EXISTS dim_supplier;
CREATE TABLE dim_supplier AS
SELECT ROW_NUMBER() OVER (ORDER BY supplier_id) AS supplier_sk, supplier_id, sap_lifnr, supplier_name, country
FROM silver_supplier
UNION ALL SELECT -1, 'UNKNOWN', NULL, 'Unknown supplier', NULL;

DROP TABLE IF EXISTS dim_customer;
CREATE TABLE dim_customer AS
SELECT ROW_NUMBER() OVER (ORDER BY customer_id) AS customer_sk, customer_id, sap_kunnr, customer_name, channel, region
FROM silver_customer
UNION ALL SELECT -1, 'UNKNOWN', NULL, 'Unknown customer', NULL, NULL;
