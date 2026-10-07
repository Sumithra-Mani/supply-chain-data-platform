-- GOLD: KPI marts, ready for Power BI / analysts

-- Customer service: OTIF, on-time, in-full and fill rate by order month and site
DROP TABLE IF EXISTS kpi_otif_monthly;
CREATE TABLE kpi_otif_monthly AS
SELECT
    d.year_month,
    s.site_name,
    COUNT(*)                                                    AS order_lines,
    ROUND(100.0 * AVG(f.is_on_time), 1)                         AS on_time_pct,
    ROUND(100.0 * AVG(f.is_in_full), 1)                         AS in_full_pct,
    ROUND(100.0 * AVG(f.is_otif), 1)                            AS otif_pct,
    ROUND(100.0 * SUM(f.qty_delivered) / SUM(f.qty_ordered), 1) AS fill_rate_pct
FROM fact_sales_order_line f
JOIN dim_date d ON d.date_sk = f.order_date_sk
JOIN dim_site s ON s.site_sk = f.site_sk
GROUP BY d.year_month, s.site_name
ORDER BY d.year_month, s.site_name;

-- Supplier performance on received purchase order lines
DROP TABLE IF EXISTS kpi_supplier_performance;
CREATE TABLE kpi_supplier_performance AS
SELECT
    sp.supplier_name,
    sp.country,
    COUNT(*)                                                         AS po_lines_received,
    ROUND(100.0 * AVG(f.is_on_time), 1)                              AS on_time_pct,
    ROUND(100.0 * AVG(f.is_in_full), 1)                              AS in_full_pct,
    ROUND(AVG(CASE WHEN f.days_late > 0 THEN f.days_late END), 1)    AS avg_days_late_when_late,
    ROUND(AVG(f.promised_lead_time_days), 1)                         AS avg_promised_lead_time_days,
    ROUND(AVG(f.actual_lead_time_days), 1)                           AS avg_actual_lead_time_days,
    RANK() OVER (ORDER BY AVG(f.is_on_time) DESC)                    AS on_time_rank
FROM fact_purchase_order_line f
JOIN dim_supplier sp ON sp.supplier_sk = f.supplier_sk
WHERE f.po_status = 'RECEIVED'
GROUP BY sp.supplier_name, sp.country
ORDER BY on_time_rank;

-- Inventory health at the latest snapshot: days of supply + stockout history
DROP TABLE IF EXISTS kpi_inventory_health;
CREATE TABLE kpi_inventory_health AS
WITH latest AS (
    SELECT MAX(date_sk) AS date_sk FROM fact_inventory_daily
),
latest_date AS (
    SELECT d.calendar_date FROM dim_date d JOIN latest l ON l.date_sk = d.date_sk
),
demand_28d AS (   -- average daily demand over the last 28 days
    SELECT f.product_sk, f.site_sk, SUM(f.qty_ordered) / 28.0 AS avg_daily_demand
    FROM fact_sales_order_line f
    JOIN dim_date d ON d.date_sk = f.order_date_sk, latest_date ld
    WHERE d.calendar_date > DATE(ld.calendar_date, '-28 days')
    GROUP BY f.product_sk, f.site_sk
),
stockout_runs AS (  -- gaps and islands: consecutive stockout days share the same group key
    SELECT i.product_sk, i.site_sk, d.calendar_date,
           JULIANDAY(d.calendar_date)
             - ROW_NUMBER() OVER (PARTITION BY i.product_sk, i.site_sk ORDER BY d.calendar_date) AS grp
    FROM fact_inventory_daily i
    JOIN dim_date d ON d.date_sk = i.date_sk
    WHERE i.is_stockout = 1
),
stockout_stats AS (
    SELECT product_sk, site_sk,
           SUM(run_days) AS stockout_days,
           MAX(run_days) AS longest_stockout_streak_days
    FROM (SELECT product_sk, site_sk, grp, COUNT(*) AS run_days
          FROM stockout_runs GROUP BY product_sk, site_sk, grp)
    GROUP BY product_sk, site_sk
)
SELECT
    p.material_id,
    p.material_name,
    p.category,
    s.site_name,
    i.qty_on_hand_ea,
    i.stock_cost_value,
    ROUND(COALESCE(dm.avg_daily_demand, 0), 1)                        AS avg_daily_demand_28d,
    ROUND(i.qty_on_hand_ea / NULLIF(dm.avg_daily_demand, 0), 1)       AS days_of_supply,
    COALESCE(so.stockout_days, 0)                                     AS stockout_days,
    COALESCE(so.longest_stockout_streak_days, 0)                      AS longest_stockout_streak_days,
    CASE
        WHEN i.qty_on_hand_ea = 0                                     THEN 'STOCKOUT'
        WHEN i.qty_on_hand_ea / NULLIF(dm.avg_daily_demand, 0) < 7     THEN 'LOW'
        WHEN i.qty_on_hand_ea / NULLIF(dm.avg_daily_demand, 0) > 90    THEN 'EXCESS'
        ELSE 'HEALTHY'
    END                                                               AS stock_status
FROM fact_inventory_daily i
JOIN latest l          ON l.date_sk = i.date_sk
JOIN dim_product p     ON p.product_sk = i.product_sk
JOIN dim_site s        ON s.site_sk = i.site_sk
LEFT JOIN demand_28d dm ON dm.product_sk = i.product_sk AND dm.site_sk = i.site_sk
LEFT JOIN stockout_stats so ON so.product_sk = i.product_sk AND so.site_sk = i.site_sk
ORDER BY days_of_supply;
