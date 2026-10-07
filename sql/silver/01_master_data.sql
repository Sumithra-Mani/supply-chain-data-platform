-- SILVER: master and reference data
-- Standardise keys (strip SAP leading zeros), tidy text, cast types.

DROP TABLE IF EXISTS silver_material;
CREATE TABLE silver_material AS
SELECT
    LTRIM(TRIM(MATNR), '0')                                         AS material_id,
    TRIM(MATNR)                                                     AS sap_matnr,
    UPPER(SUBSTR(TRIM(MAKTX), 1, 1)) || LOWER(SUBSTR(TRIM(MAKTX), 2)) AS material_name,
    TRIM(MTART)                                                     AS material_type,
    UPPER(TRIM(MATKL))                                              AS category,
    TRIM(MEINS)                                                     AS base_uom,
    CAST(UMREZ AS INTEGER)                                          AS units_per_case,
    CAST(STPRS AS REAL)                                             AS standard_cost,
    CAST(MHDHB AS INTEGER)                                          AS shelf_life_days,
    LTRIM(TRIM(LIFNR), '0')                                         AS primary_supplier_id
FROM bronze_sap_material_master
WHERE MATNR IS NOT NULL;

DROP TABLE IF EXISTS silver_supplier;
CREATE TABLE silver_supplier AS
SELECT LTRIM(TRIM(LIFNR), '0') AS supplier_id, TRIM(LIFNR) AS sap_lifnr,
       TRIM(NAME1) AS supplier_name, TRIM(LAND1) AS country
FROM bronze_sap_suppliers;

DROP TABLE IF EXISTS silver_customer;
CREATE TABLE silver_customer AS
SELECT LTRIM(TRIM(KUNNR), '0') AS customer_id, TRIM(KUNNR) AS sap_kunnr,
       TRIM(NAME1) AS customer_name, TRIM(KTGRD) AS channel, TRIM(REGIO) AS region
FROM bronze_sap_customers;

-- Reference data: WMS warehouse codes do not match SAP plant codes
DROP TABLE IF EXISTS silver_site;
CREATE TABLE silver_site AS
SELECT TRIM(SAP_PLANT) AS site_id, TRIM(WMS_WH_CODE) AS wms_wh_code,
       TRIM(SITE_NAME) AS site_name, TRIM(COUNTRY) AS country
FROM bronze_ref_site_mapping;

-- Cross-reference: one product, two systems (SAP MATNR vs WMS SKU)
DROP TABLE IF EXISTS silver_material_xref;
CREATE TABLE silver_material_xref AS
SELECT
    m.material_id,
    m.sap_matnr,
    TRIM(w.SKU)                                   AS wms_sku,
    m.units_per_case                              AS sap_units_per_case,
    CAST(w.CASE_QTY AS INTEGER)                   AS wms_case_qty,
    CASE WHEN m.units_per_case = CAST(w.CASE_QTY AS INTEGER) THEN 1 ELSE 0 END AS case_qty_matches
FROM silver_material m
LEFT JOIN bronze_wms_items w
       ON LTRIM(TRIM(w.SKU), '0') = m.material_id;
