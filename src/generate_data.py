"""
Synthetic supply chain source-system extracts.

Simulates one year of daily operations for a distributor with three
distribution centres, then writes the data out the way real source systems
would deliver it: SAP-style ERP extracts (material master, purchase orders,
goods receipts, sales orders, deliveries) and a separate warehouse management
system (WMS) with its own codes and units.

Real-world data problems are injected on purpose so the pipeline has to
handle them:
  * SAP numbers are zero-padded (MATNR '000000000010000123'), WMS SKUs are not
  * WMS site codes differ from SAP plant codes (needs a reference mapping)
  * One WMS site reports stock in cases (CS) instead of eaches (EA)
  * ~1% of order lines are re-extracted, so they appear twice
  * A few rows have invalid quantities, missing or unknown material numbers,
    or goods receipts posted before the PO was raised
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

CATEGORIES = ["Tablets", "Liquids", "Topicals", "Devices", "Nutrition"]
CHANNELS = ["Pharmacy", "Hospital", "Wholesaler", "Online"]
REGIONS = ["North", "Midlands", "South", "Scotland", "Ireland"]
SITES = [
    # SAP plant, WMS warehouse code, name, country, stock unit reported by the WMS
    ("UK10", "MID", "Midlands DC", "GB", "EA"),
    ("UK20", "NWE", "North West DC", "GB", "EA"),
    ("IE10", "DUB", "Dublin DC", "IE", "CS"),
]
SITE_FACTOR = {"UK10": 1.0, "UK20": 0.7, "IE10": 0.4}
CASE_SIZES = [6, 12, 24, 48]
REQUESTED_LEAD_DAYS = 3  # customers ask for delivery 3 days after ordering


@dataclass
class Config:
    out_dir: Path = Path("data/raw")
    start: date = date(2025, 10, 1)
    days: int = 365
    n_products: int = 120
    n_suppliers: int = 20
    n_customers: int = 60
    seed: int = 42


def sap_pad(num: int, width: int = 18) -> str:
    """SAP stores numeric keys left-padded with zeros."""
    return str(num).zfill(width)


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def build_master_data(cfg: Config, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    suppliers = []
    for i in range(cfg.n_suppliers):
        suppliers.append({
            "LIFNR": sap_pad(100001 + i, 10),
            "NAME1": f"Supplier {i + 1:02d} Ltd",
            "LAND1": rng.choice(["GB", "IE", "DE", "NL", "IN", "CN", "US"]),
            "lead_time_days": int(rng.integers(5, 22)),
            "late_prob": float(rng.uniform(0.03, 0.35)),
        })
    sup = pd.DataFrame(suppliers)

    products = []
    for i in range(cfg.n_products):
        case_qty = int(rng.choice(CASE_SIZES))
        products.append({
            "material_num": 10000100 + i,
            "MAKTX": f"{rng.choice(CATEGORIES)} product {i + 1:03d}",
            "MATKL": None,
            "MTART": rng.choice(["FERT", "HAWA"], p=[0.7, 0.3]),
            "MEINS": "EA",
            "UMREZ": case_qty,
            "standard_cost": round(float(rng.uniform(0.8, 45.0)), 2),
            "shelf_life_days": int(rng.choice([365, 540, 730, 1095])),
            "LIFNR": sup.LIFNR.iloc[int(rng.integers(0, len(sup)))],
            "base_demand": float(np.exp(rng.uniform(math.log(2), math.log(40)))),
        })
    prod = pd.DataFrame(products)
    prod["MATKL"] = prod.MAKTX.str.split(" ").str[0].str.upper()

    customers = pd.DataFrame({
        "KUNNR": [sap_pad(200001 + i, 10) for i in range(cfg.n_customers)],
        "NAME1": [f"Customer {i + 1:03d}" for i in range(cfg.n_customers)],
        "channel": rng.choice(CHANNELS, cfg.n_customers),
        "region": rng.choice(REGIONS, cfg.n_customers),
    })
    return {"suppliers": sup, "products": prod, "customers": customers}


def simulate(cfg: Config, md: dict[str, pd.DataFrame], rng: np.random.Generator) -> dict[str, list]:
    """Day-by-day inventory simulation with reorder-point purchasing."""
    prod, sup, cust = md["products"], md["suppliers"], md["customers"]
    sup_idx = sup.set_index("LIFNR")
    plants = [s[0] for s in SITES]

    # State per (product index, plant)
    stock, open_po_qty, rop, order_qty = {}, {}, {}, {}
    for p in prod.itertuples():
        lt = int(sup_idx.loc[p.LIFNR, "lead_time_days"])
        for plant in plants:
            mean = p.base_demand * SITE_FACTOR[plant]
            k = (p.Index, plant)
            rop[k] = mean * (lt + 3)                                  # lead-time demand + a thin safety buffer
            order_qty[k] = max(p.UMREZ, math.ceil(mean * 30 / p.UMREZ) * p.UMREZ)  # ~30 days, whole cases
            stock[k] = round(rop[k] * 1.5)
            open_po_qty[k] = 0

    receipts: dict[date, list] = {}
    out = {"so": [], "dlv": [], "po": [], "gr": [], "inv": []}
    so_num, dlv_num, po_num, gr_num = 1000000, 80000000, 4500000000, 5000000000

    for d_i in range(cfg.days):
        day = cfg.start + timedelta(days=d_i)
        season = 1 + 0.25 * math.sin(2 * math.pi * (d_i - 60) / 365)   # winter peak
        weekday = 0.35 if day.weekday() >= 5 else 1.0

        # 1. Goods receipts arriving today
        for (k, po_id, qty, lifnr) in receipts.pop(day, []):
            stock[k] += qty
            open_po_qty[k] -= qty
            gr_num += 1
            out["gr"].append({"MBLNR": str(gr_num), "EBELN": str(po_id), "EBELP": "00010",
                              "BUDAT": _iso(day), "MENGE": qty})

        for plant in plants:
            orders_today: dict[str, int] = {}   # customer -> sales order number (one header per customer/day/site)
            line_counter: dict[int, int] = {}
            for p in prod.itertuples():
                k = (p.Index, plant)
                demand = int(rng.poisson(p.base_demand * SITE_FACTOR[plant] * season * weekday))

                # 2. Customer orders: split demand into order lines, fill from stock
                while demand > 0:
                    qty = int(min(demand, rng.integers(4, 30)))
                    demand -= qty
                    kunnr = cust.KUNNR.iloc[int(rng.integers(0, len(cust)))]
                    if kunnr not in orders_today:
                        so_num += 1
                        orders_today[kunnr] = so_num
                    vbeln = orders_today[kunnr]
                    line_counter[vbeln] = line_counter.get(vbeln, 0) + 10
                    posnr = line_counter[vbeln]
                    shipped = min(qty, stock[k])
                    stock[k] -= shipped
                    out["so"].append({"VBELN": sap_pad(vbeln, 10), "POSNR": str(posnr).zfill(6),
                                      "KUNNR": kunnr, "MATNR": sap_pad(p.material_num), "WERKS": plant,
                                      "AUDAT": _iso(day),
                                      "VDATU": _iso(day + timedelta(days=REQUESTED_LEAD_DAYS)),
                                      "KWMENG": qty, "VRKME": "EA"})
                    if shipped > 0:
                        transit = int(rng.choice([1, 2, 3, 4, 5, 6], p=[0.25, 0.38, 0.25, 0.07, 0.04, 0.01]))
                        if plant == "IE10" and rng.random() < 0.25:          # sea crossing to Ireland
                            transit += 1
                        if day.month in (12, 1) and rng.random() < 0.3:      # winter peak congestion
                            transit += 1
                        dlv_num += 1
                        out["dlv"].append({"VBELN_VL": sap_pad(dlv_num, 10), "VGBEL": sap_pad(vbeln, 10),
                                           "VGPOS": str(posnr).zfill(6),
                                           "WADAT_IST": _iso(day + timedelta(days=transit)),
                                           "LFIMG": shipped})

                # 3. Reorder when stock + open POs fall to the reorder point
                if stock[k] + open_po_qty[k] <= rop[k]:
                    s = sup_idx.loc[p.LIFNR]
                    promised = day + timedelta(days=int(s.lead_time_days))
                    late = rng.random() < s.late_prob
                    delay = int(rng.integers(1, 12)) if late else int(rng.choice([0, 0, 0, -1]))
                    qty = order_qty[k]
                    received = qty if rng.random() > 0.05 else int(qty * 0.9)
                    po_num += 1
                    open_po_qty[k] += received
                    out["po"].append({"EBELN": str(po_num), "EBELP": "00010", "LIFNR": p.LIFNR,
                                      "MATNR": sap_pad(p.material_num), "WERKS": plant,
                                      "BEDAT": _iso(day), "EINDT": _iso(promised),
                                      "MENGE": qty, "MEINS": "EA"})
                    arrival = promised + timedelta(days=delay)
                    receipts.setdefault(arrival, []).append((k, po_num, received, p.LIFNR))

                # 4. End-of-day stock snapshot from the WMS
                out["inv"].append((day, plant, p.Index, stock[k]))
    return out


def inject_issues(df: pd.DataFrame, rng: np.random.Generator, dup_rate: float, extracted_at: datetime) -> pd.DataFrame:
    """Add an extraction timestamp and re-extract a sample of rows (duplicates)."""
    df = df.copy()
    df["EXTRACTED_AT"] = extracted_at.isoformat(timespec="seconds")
    dups = df.sample(frac=dup_rate, random_state=int(rng.integers(0, 1_000_000))).copy()
    dups["EXTRACTED_AT"] = (extracted_at + timedelta(hours=6)).isoformat(timespec="seconds")
    return pd.concat([df, dups]).sort_index(kind="stable").reset_index(drop=True)


def write_extracts(cfg: Config, md: dict[str, pd.DataFrame], sim: dict[str, list], rng: np.random.Generator) -> dict[str, int]:
    out = cfg.out_dir
    out.mkdir(parents=True, exist_ok=True)
    prod, sup, cust = md["products"], md["suppliers"], md["customers"]
    extracted_at = datetime.combine(cfg.start + timedelta(days=cfg.days), datetime.min.time()) + timedelta(hours=2)

    # --- SAP master data ---
    mara = prod.assign(MATNR=prod.material_num.map(sap_pad))
    mara["MAKTX"] = mara.MAKTX.where(rng.random(len(mara)) > 0.1, "  " + mara.MAKTX.str.upper() + " ")  # messy text
    mara[["MATNR", "MAKTX", "MTART", "MATKL", "MEINS", "UMREZ", "standard_cost", "shelf_life_days", "LIFNR"]] \
        .rename(columns={"standard_cost": "STPRS", "shelf_life_days": "MHDHB"}) \
        .to_csv(out / "sap_material_master.csv", index=False)
    sup[["LIFNR", "NAME1", "LAND1"]].to_csv(out / "sap_suppliers.csv", index=False)
    cust.rename(columns={"channel": "KTGRD", "region": "REGIO"}).to_csv(out / "sap_customers.csv", index=False)

    # --- SAP transactions (with duplicates and bad rows) ---
    so = pd.DataFrame(sim["so"])
    bad = so.sample(n=max(3, len(so) // 1000), random_state=1).index
    so.loc[bad[: len(bad) // 3], "KWMENG"] = 0                                   # invalid quantity
    so.loc[bad[len(bad) // 3: 2 * len(bad) // 3], "MATNR"] = sap_pad(99999999)  # unknown material (orphan)
    so.loc[bad[2 * len(bad) // 3:], "MATNR"] = None                             # missing material
    inject_issues(so, rng, 0.01, extracted_at).to_csv(out / "sap_sales_orders.csv", index=False)
    inject_issues(pd.DataFrame(sim["dlv"]), rng, 0.01, extracted_at).to_csv(out / "sap_deliveries.csv", index=False)

    po = pd.DataFrame(sim["po"])
    inject_issues(po, rng, 0.01, extracted_at).to_csv(out / "sap_purchase_orders.csv", index=False)
    gr = pd.DataFrame(sim["gr"])
    po_dates = po.set_index("EBELN").BEDAT
    early = gr.sample(n=max(2, len(gr) // 500), random_state=2).index
    gr.loc[early, "BUDAT"] = [(date.fromisoformat(po_dates[e]) - timedelta(days=3)).isoformat()
                              for e in gr.loc[early, "EBELN"]]                  # receipt before PO date
    inject_issues(gr, rng, 0.0, extracted_at).to_csv(out / "sap_goods_receipts.csv", index=False)

    # --- WMS: own codes, unpadded SKUs, one site reports in cases ---
    wms_site = {s[0]: (s[1], s[4]) for s in SITES}
    inv = pd.DataFrame(sim["inv"], columns=["day", "plant", "pidx", "qty"])
    inv["SKU"] = prod.material_num.values[inv.pidx].astype(str)
    inv["WH_CODE"] = inv.plant.map(lambda p: wms_site[p][0])
    inv["UOM"] = inv.plant.map(lambda p: wms_site[p][1])
    case = prod.UMREZ.values[inv.pidx]
    inv["QTY_ON_HAND"] = np.where(inv.UOM == "CS", np.round(inv.qty / case, 3), inv.qty)
    inv["SNAPSHOT_DATE"] = inv.day.map(lambda d: d.isoformat())
    inv[["SNAPSHOT_DATE", "WH_CODE", "SKU", "QTY_ON_HAND", "UOM"]].to_csv(out / "wms_inventory_snapshots.csv", index=False)
    wms_items = pd.DataFrame({"SKU": prod.material_num.astype(str), "DESCRIPTION": prod.MAKTX.str.lower(),
                              "CASE_QTY": prod.UMREZ})
    wrong = wms_items.sample(n=3, random_state=3).index           # master data drift between systems
    wms_items.loc[wrong, "CASE_QTY"] = wms_items.loc[wrong, "CASE_QTY"] * 2
    wms_items.to_csv(out / "wms_items.csv", index=False)

    # --- Reference data maintained by the business ---
    pd.DataFrame([{"WMS_WH_CODE": s[1], "SAP_PLANT": s[0], "SITE_NAME": s[2], "COUNTRY": s[3]} for s in SITES]) \
        .to_csv(out / "ref_site_mapping.csv", index=False)

    counts = {}
    for f in sorted(out.glob("*.csv")):
        with open(f) as fh:
            counts[f.name] = sum(1 for _ in fh) - 1
    return counts


def generate(cfg: Config) -> dict[str, int]:
    rng = np.random.default_rng(cfg.seed)
    md = build_master_data(cfg, rng)
    sim = simulate(cfg, md, rng)
    return write_extracts(cfg, md, sim, rng)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate synthetic SAP/WMS supply chain extracts")
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--products", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    counts = generate(Config(out_dir=Path(a.out), days=a.days, n_products=a.products, seed=a.seed))
    for name, n in counts.items():
        print(f"{name:32s} {n:>8,} rows")
