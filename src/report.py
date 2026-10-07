"""Export gold KPI marts to CSV (for Power BI / Excel) and draw summary charts."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

EXPORTS = ["kpi_otif_monthly", "kpi_supplier_performance", "kpi_inventory_health", "dq_results"]


def export_reports(conn: sqlite3.Connection, out_dir: Path, charts: bool = True) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for table in EXPORTS:
        df = pd.read_sql(f"SELECT * FROM {table}", conn)
        df.to_csv(out_dir / f"{table}.csv", index=False)
        log.info("  %-28s %6d rows", f"{table}.csv", len(df))

    summary = pd.read_sql("""
        SELECT ROUND(100.0 * AVG(is_otif), 1)                          AS otif_pct,
               ROUND(100.0 * SUM(qty_delivered) / SUM(qty_ordered), 1) AS fill_rate_pct,
               COUNT(*)                                                AS order_lines
        FROM fact_sales_order_line""", conn).iloc[0]
    status = pd.read_sql("SELECT stock_status, COUNT(*) AS n FROM kpi_inventory_health GROUP BY 1", conn)
    log.info("  Network OTIF %.1f%% | fill rate %.1f%% | %s order lines",
             summary.otif_pct, summary.fill_rate_pct, f"{int(summary.order_lines):,}")
    log.info("  Stock status today: %s", ", ".join(f"{r.stock_status}={r.n}" for r in status.itertuples()))

    if charts:
        _charts(conn, out_dir)


def _charts(conn: sqlite3.Connection, out_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, muted, grid = "#1f2328", "#59636e", "#d8dee4"
    blue, orange = "#2a6fdb", "#e0832c"
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": grid, "axes.labelcolor": muted,
                         "xtick.color": muted, "ytick.color": muted})

    # 1. OTIF by site and order month
    m = pd.read_sql("SELECT year_month, site_name, otif_pct FROM kpi_otif_monthly", conn) \
          .pivot(index="year_month", columns="site_name", values="otif_pct")
    fig, ax = plt.subplots(figsize=(8, 3.8), dpi=150)
    palette = {"Dublin DC": orange, "Midlands DC": blue, "North West DC": "#7a8a99"}
    x = range(len(m))
    label_y, min_gap = {}, 1.2   # keep end labels from overlapping
    for site in sorted(m.columns, key=lambda c: -m[c].iloc[-1]):
        y = m[site].iloc[-1]
        if label_y and min(label_y.values()) - y < min_gap:
            y = min(label_y.values()) - min_gap
        label_y[site] = y
    for site in m.columns:
        color = palette.get(site, muted)
        ax.plot(x, m[site], color=color, lw=2.2 if site == "Dublin DC" else 1.6, marker="o", ms=3)
        ax.text(len(m) - 0.6, label_y[site], site, color=color, va="center", fontsize=9)
    ax.set_xticks(list(x), m.index, rotation=45)
    ax.set_ylabel("OTIF % of order lines")
    ax.set_xlim(-0.5, len(m) + 1.6)
    ax.grid(axis="y", color=grid, lw=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("OTIF dips every winter, and Dublin trails the UK sites all year", loc="left",
                 color=ink, fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out_dir / "otif_trend.png")
    plt.close(fig)

    # 2. Supplier on-time delivery ranking
    s = pd.read_sql("SELECT supplier_name, on_time_pct FROM kpi_supplier_performance ORDER BY on_time_pct", conn)
    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
    colors = [orange if v < 80 else blue for v in s.on_time_pct]
    ax.barh(s.supplier_name, s.on_time_pct, color=colors, height=0.65, zorder=2)
    ax.axvline(80, color=muted, lw=1, ls="--", zorder=0)
    ax.text(80.5, -0.9, "80% target", color=muted, fontsize=9)
    for y, v in enumerate(s.on_time_pct):
        ax.text(v + 0.8, y, f"{v:.0f}%", va="center", fontsize=8.5, color=ink,
                bbox=dict(facecolor="white", edgecolor="none", pad=0.5))
    ax.set_xlim(0, 105)
    ax.set_xlabel("PO lines received on or before promised date (%)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("Supplier on-time delivery (orange = below 80% target)", loc="left",
                 color=ink, fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out_dir / "supplier_on_time.png")
    plt.close(fig)
    log.info("  otif_trend.png, supplier_on_time.png")
