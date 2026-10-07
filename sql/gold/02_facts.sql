-- GOLD: fact tables. Grain is stated first for each.

-- Grain: one sales order line (transaction fact)
DROP TABLE IF EXISTS fact_sales_order_line;
CREATE TABLE fact_sales_order_line AS
WITH dlv AS (
    SELECT order_id, line_no,
           SUM(qty_delivered)  AS qty_delivered,
           MAX(delivery_date)  AS last_delivery_date,
           COUNT(*)            AS delivery_count
    FROM silver_delivery
    GROUP BY order_id, line_no
)
SELECT
    so.order_id,
    so.line_no,
    COALESCE(p.product_sk, -1)                                    AS product_sk,
    COALESCE(s.site_sk, -1)                                       AS site_sk,
    COALESCE(c.customer_sk, -1)                                   AS customer_sk,
    CAST(STRFTIME('%Y%m%d', so.order_date) AS INTEGER)            AS order_date_sk,
    CAST(STRFTIME('%Y%m%d', so.requested_date) AS INTEGER)        AS requested_date_sk,
    CAST(STRFTIME('%Y%m%d', dlv.last_delivery_date) AS INTEGER)   AS delivery_date_sk,
    so.qty_ordered,
    COALESCE(dlv.qty_delivered, 0)                                AS qty_delivered,
    ROUND(COALESCE(dlv.qty_delivered, 0) * p.standard_cost, 2)    AS delivered_cost_value,
    CASE WHEN dlv.order_id IS NOT NULL THEN 1 ELSE 0 END          AS is_delivered,
    CASE WHEN dlv.last_delivery_date <= so.requested_date THEN 1 ELSE 0 END AS is_on_time,
    CASE WHEN COALESCE(dlv.qty_delivered, 0) >= so.qty_ordered THEN 1 ELSE 0 END AS is_in_full,
    CASE WHEN dlv.last_delivery_date <= so.requested_date
          AND COALESCE(dlv.qty_delivered, 0) >= so.qty_ordered THEN 1 ELSE 0 END AS is_otif,
    CAST(JULIANDAY(dlv.last_delivery_date) - JULIANDAY(so.requested_date) AS INTEGER) AS days_late
FROM silver_sales_order_line so
LEFT JOIN dlv           ON dlv.order_id = so.order_id AND dlv.line_no = so.line_no
LEFT JOIN dim_product p ON p.material_id = so.material_id
LEFT JOIN dim_site s    ON s.site_id = so.site_id
LEFT JOIN dim_customer c ON c.customer_id = so.customer_id;

-- Grain: one purchase order line (accumulating snapshot: ordered -> promised -> received)
DROP TABLE IF EXISTS fact_purchase_order_line;
CREATE TABLE fact_purchase_order_line AS
WITH gr AS (
    SELECT po_id, po_line,
           SUM(qty_received) AS qty_received,
           MIN(receipt_date) AS first_receipt_date,
           MAX(receipt_date) AS last_receipt_date
    FROM silver_goods_receipt
    GROUP BY po_id, po_line
)
SELECT
    po.po_id,
    po.po_line,
    COALESCE(sp.supplier_sk, -1)                                  AS supplier_sk,
    COALESCE(p.product_sk, -1)                                    AS product_sk,
    COALESCE(s.site_sk, -1)                                       AS site_sk,
    CAST(STRFTIME('%Y%m%d', po.order_date) AS INTEGER)            AS order_date_sk,
    CAST(STRFTIME('%Y%m%d', po.promised_date) AS INTEGER)         AS promised_date_sk,
    CAST(STRFTIME('%Y%m%d', gr.last_receipt_date) AS INTEGER)     AS receipt_date_sk,
    po.qty_ordered,
    COALESCE(gr.qty_received, 0)                                  AS qty_received,
    ROUND(po.qty_ordered * p.standard_cost, 2)                    AS order_cost_value,
    CASE WHEN gr.po_id IS NULL THEN 'OPEN' ELSE 'RECEIVED' END    AS po_status,
    CAST(JULIANDAY(po.promised_date) - JULIANDAY(po.order_date) AS INTEGER)       AS promised_lead_time_days,
    CAST(JULIANDAY(gr.last_receipt_date) - JULIANDAY(po.order_date) AS INTEGER)   AS actual_lead_time_days,
    CAST(JULIANDAY(gr.last_receipt_date) - JULIANDAY(po.promised_date) AS INTEGER) AS days_late,
    CASE WHEN gr.last_receipt_date <= po.promised_date THEN 1 ELSE 0 END          AS is_on_time,
    CASE WHEN COALESCE(gr.qty_received, 0) >= po.qty_ordered THEN 1 ELSE 0 END    AS is_in_full
FROM silver_purchase_order_line po
LEFT JOIN gr            ON gr.po_id = po.po_id AND gr.po_line = po.po_line
LEFT JOIN dim_supplier sp ON sp.supplier_id = po.supplier_id
LEFT JOIN dim_product p ON p.material_id = po.material_id
LEFT JOIN dim_site s    ON s.site_id = po.site_id;

-- Grain: one product x site x day (periodic snapshot; stock is semi-additive)
DROP TABLE IF EXISTS fact_inventory_daily;
CREATE TABLE fact_inventory_daily AS
SELECT
    CAST(STRFTIME('%Y%m%d', i.snapshot_date) AS INTEGER)  AS date_sk,
    COALESCE(p.product_sk, -1)                            AS product_sk,
    COALESCE(s.site_sk, -1)                               AS site_sk,
    i.qty_on_hand_ea,
    ROUND(i.qty_on_hand_ea * p.standard_cost, 2)          AS stock_cost_value,
    CASE WHEN i.qty_on_hand_ea = 0 THEN 1 ELSE 0 END      AS is_stockout
FROM silver_inventory_daily i
LEFT JOIN dim_product p ON p.material_id = i.material_id
LEFT JOIN dim_site s    ON s.site_id = i.site_id;

CREATE INDEX IF NOT EXISTS ix_fsol_order ON fact_sales_order_line(order_date_sk);
CREATE INDEX IF NOT EXISTS ix_finv_ps    ON fact_inventory_daily(product_sk, site_sk, date_sk);
