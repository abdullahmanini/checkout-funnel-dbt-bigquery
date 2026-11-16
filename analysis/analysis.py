"""Reads the dbt marts from BigQuery and answers the three product questions.

Usage: GCP_PROJECT=<your-project> python analysis/analysis.py
Writes outputs/results.json, CSV snapshots of the marts and the README charts.
"""
import json
import math
import os
from datetime import date
from pathlib import Path

import matplotlib
import matplotlib.ticker
import pandas as pd
from google.cloud import bigquery
from scipy import stats

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
PROJECT = os.environ["GCP_PROJECT"]
DATASET = f"{PROJECT}.checkout_funnel"
YEAR_START, YEAR_END = "2025-11-01", "2026-10-31"  # trailing 12 months before as_of

client = bigquery.Client(project=PROJECT)


def q(sql):
    return client.query(sql).to_dataframe()


def two_prop(x_c, n_c, x_t, n_t):
    p_c, p_t = x_c / n_c, x_t / n_t
    pooled = (x_c + x_t) / (n_c + n_t)
    z = (p_t - p_c) / math.sqrt(pooled * (1 - pooled) * (1 / n_c + 1 / n_t))
    se = math.sqrt(p_c * (1 - p_c) / n_c + p_t * (1 - p_t) / n_t)
    diff = p_t - p_c
    return {
        "control": p_c, "treatment": p_t, "diff": diff,
        "ci": [diff - 1.96 * se, diff + 1.96 * se],
        "rel": diff / p_c, "rel_ci": [(diff - 1.96 * se) / p_c, (diff + 1.96 * se) / p_c],
        "z": z, "p": 2 * stats.norm.sf(abs(z)),
    }


def welch(c, t):
    c, t = c.astype(float), t.astype(float)
    res = stats.ttest_ind(t, c, equal_var=False)
    diff = t.mean() - c.mean()
    se = math.sqrt(t.var(ddof=1) / len(t) + c.var(ddof=1) / len(c))
    crit = stats.t.ppf(0.975, res.df)
    return {
        "control": c.mean(), "treatment": t.mean(), "diff": diff,
        "ci": [diff - crit * se, diff + crit * se],
        "rel": diff / c.mean(), "rel_ci": [(diff - crit * se) / c.mean(), (diff + crit * se) / c.mean()],
        "t": res.statistic, "p": res.pvalue,
        "mann_whitney_p": stats.mannwhitneyu(t, c, alternative="two-sided").pvalue,
    }


# ---------- 1. Funnel bottleneck ----------
FUNNEL_WHERE = f"session_date between '{YEAR_START}' and '{YEAR_END}'"
funnel = q(f"""
    select is_anonymous,
           count(*) as sessions,
           countif(reached_product) as product,
           countif(reached_cart) as cart,
           countif(purchased) as purchase
    from `{DATASET}.fct_checkout_funnel` where {FUNNEL_WHERE}
    group by is_anonymous""")
segments = q(f"""
    select 'browser' as dim, browser as value, count(*) n,
           avg(cast(reached_cart as int64)) cart_rate, avg(cast(purchased as int64)) conversion
    from `{DATASET}.fct_checkout_funnel` where {FUNNEL_WHERE} group by 1, 2
    union all
    select 'traffic_source', traffic_source, count(*), avg(cast(reached_cart as int64)), avg(cast(purchased as int64))
    from `{DATASET}.fct_checkout_funnel` where {FUNNEL_WHERE} group by 1, 2
    union all
    select 'category', category, count(*), avg(cast(reached_cart as int64)), avg(cast(purchased as int64))
    from `{DATASET}.fct_checkout_funnel` where {FUNNEL_WHERE} group by 1, 2""")
timing = q(f"""
    select approx_quantiles(seconds_product_to_cart, 2)[offset(1)] as median_seconds_product_to_cart,
           approx_quantiles(minutes_cart_to_purchase, 2)[offset(1)] as median_minutes_cart_to_purchase,
           approx_quantiles(minutes_cart_to_purchase, 10)[offset(9)] as p90_minutes_cart_to_purchase
    from `{DATASET}.fct_checkout_funnel` where {FUNNEL_WHERE}""").iloc[0]
raw = q("""
    select count(*) as events,
           countif(created_at > current_timestamp()) as future_dated_events
    from `bigquery-public-data.thelook_ecommerce.events`""").iloc[0]

tot = funnel[["sessions", "product", "cart", "purchase"]].sum()
anon = funnel[funnel.is_anonymous].iloc[0]
signed = funnel[~funnel.is_anonymous].iloc[0]
steps = [
    {"step": "Product view to cart", "from": int(tot["product"]), "to": int(tot["cart"])},
    {"step": "Cart to purchase", "from": int(tot["cart"]), "to": int(tot["purchase"])},
]
for s in steps:
    s["lost"] = s["from"] - s["to"]
    s["drop_off"] = s["lost"] / s["from"]

seg_summary = {}
for dim, g in segments.groupby("dim"):
    seg_summary[dim] = {
        "segments": len(g),
        "cart_rate_range": [g.cart_rate.min(), g.cart_rate.max()],
        "conversion_range": [g.conversion.min(), g.conversion.max()],
    }

# ---------- 2. Simulated A/B test ----------
ex = q(f"select * from `{DATASET}.mart_experiment_visitors`")
ex["revenue"] = ex.revenue.astype(float)
ex["potential_guest_revenue"] = ex.potential_guest_revenue.astype(float)
c, t = ex[ex.arm == "control"], ex[ex.arm == "treatment"]
srm = stats.chisquare([len(c), len(t)])
conv = two_prop(c.converted.sum(), len(c), t.converted.sum(), len(t))
rpv = welch(c.revenue, t.revenue)
buyers_c, buyers_t = c[c.orders > 0], t[t.orders > 0]
aov = welch(buyers_c.revenue / buyers_c.orders, buyers_t.revenue / buyers_t.orders)
rep_c, rep_t = c[c.converted], t[t.converted]
repeat = two_prop(rep_c.repeat_30d.sum(), len(rep_c), rep_t.repeat_30d.sum(), len(rep_t))

# Every effect in this test comes from the simulated guest orders, so the true lift is the
# counterfactual guest outcome averaged over all visitors (computed for both arms in the mart).
true_conv = ex.would_recover.mean()
true_rpv = ex.potential_guest_revenue.mean()

# Segment check: is the conversion lift concentrated anywhere?
by_source = []
for src, g in ex.groupby("traffic_source"):
    gc, gt = g[g.arm == "control"], g[g.arm == "treatment"]
    r = two_prop(gc.converted.sum(), len(gc), gt.converted.sum(), len(gt))
    by_source.append({"traffic_source": src, "visitors": len(g), "diff": r["diff"], "ci": r["ci"], "p": r["p"]})
by_source.sort(key=lambda r: r["p"])
m = len(by_source)
for i, r in enumerate(by_source):  # Holm step-down
    r["holm_p"] = min(1.0, max((m - j) * by_source[j]["p"] for j in range(i + 1)))

# ---------- 3. Projected annual GMV impact ----------
monthly = q(f"select * from `{DATASET}.mart_monthly_kpis` order by month")
year = monthly[(monthly.month >= pd.Timestamp(YEAR_START).date()) & (monthly.month <= pd.Timestamp(YEAR_END).date())]
annual_gmv = float(year.gmv.astype(float).sum())
gmv_impact = {
    "trailing_12m_gmv": annual_gmv,
    "rel": rpv["rel"], "rel_ci": rpv["rel_ci"],
    "dollars": annual_gmv * rpv["rel"],
    "dollars_ci": [annual_gmv * x for x in rpv["rel_ci"]],
    "true_rel": true_rpv / c.revenue.mean(),
}

merchants = q(f"select * from `{DATASET}.mart_merchant_performance` order by gmv desc")

# ---------- self-checks ----------
# Pooled two-proportion z squared equals the uncorrected chi-square on the 2x2 table.
table = [[c.converted.sum(), len(c) - c.converted.sum()], [t.converted.sum(), len(t) - t.converted.sum()]]
assert abs(conv["z"] ** 2 - stats.chi2_contingency(table, correction=False)[0]) < 1e-6
assert len(ex) == ex.visitor_id.nunique()
assert (c.guest_orders == 0).all()

results = {
    "run_date": date.today().isoformat(),
    "window": {"funnel": [YEAR_START, YEAR_END], "experiment": ["2026-08-03", "2026-09-27"]},
    "data_quality": {
        "raw_events": int(raw.events),
        "future_dated_events": int(raw.future_dated_events),
        "dbt_tests": 37,
    },
    "funnel": {
        "sessions": int(tot.sessions),
        "signed_in": {k: int(signed[k]) for k in ["sessions", "cart", "purchase"]},
        "anonymous": {k: int(anon[k]) for k in ["sessions", "cart", "purchase"]},
        "steps": steps,
        "segments": seg_summary,
        "timing": {k: float(v) for k, v in timing.items()},
    },
    "experiment": {
        "visitors": {"control": len(c), "treatment": len(t)},
        "srm_p": srm.pvalue,
        "guest_orders": int(t.guest_orders.sum()),
        "conversion": conv, "conversion_true_diff": true_conv,
        "revenue_per_visitor": rpv, "rpv_true_diff": true_rpv,
        "aov": aov,
        "repeat_30d": repeat,
        "by_traffic_source": by_source,
    },
    "gmv_impact": gmv_impact,
    "monthly": [
        {"month": str(r.month), "gmv": float(r.gmv), "conversion": float(r.session_conversion_rate),
         "aov": float(r.aov),
         "repeat_30d": None if pd.isna(r.new_buyer_repeat_rate_30d) else float(r.new_buyer_repeat_rate_30d)}
        for r in year.itertuples()
    ],
    "top_merchants": [
        {"brand": r.brand, "gmv": float(r.gmv), "orders": int(r.orders), "aov": float(r.aov),
         "conversion": float(r.conversion_rate or 0), "return_rate": float(r.return_rate or 0),
         "repeat_30d": float(r.repeat_rate_30d or 0)}
        for r in merchants.head(10).itertuples()
    ],
}

OUT.mkdir(exist_ok=True)
(OUT / "results.json").write_text(json.dumps(results, indent=2, default=float))
ex.to_csv(OUT / "mart_experiment_visitors.csv", index=False)
monthly.to_csv(OUT / "mart_monthly_kpis.csv", index=False)
merchants.to_csv(OUT / "mart_merchant_performance.csv", index=False)
segments.to_csv(OUT / "funnel_segments.csv", index=False)

# ---------- charts ----------
INK, CREAM, ALOE, SHADE = "#000000", "#fbfbf5", "#5fd38d", "#a1a1aa"
plt.rcParams.update({"font.family": "Helvetica", "font.size": 11, "axes.edgecolor": SHADE,
                     "axes.spines.top": False, "axes.spines.right": False})

fig, ax = plt.subplots(figsize=(8, 3.2), facecolor=CREAM)
ax.set_facecolor(CREAM)
labels = ["Viewed a product", "Added to cart", "Purchased"]
for i, (name, row, color) in enumerate([("Signed in", signed, INK), ("Anonymous", anon, SHADE)]):
    vals = [row["product"], row["cart"], row["purchase"]]
    y = [k + i * 0.38 for k in range(3)]
    ax.barh(y, vals, height=0.36, color=color, label=name)
    for yy, v in zip(y, vals):
        ax.text(v + 600, yy, f"{int(v):,}", va="center", fontsize=9)
ax.set_yticks([k + 0.19 for k in range(3)], labels)
ax.invert_yaxis()
ax.set_xlabel("Sessions, Nov 2025 to Oct 2026")
ax.xaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
ax.legend(frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2)
fig.tight_layout()
fig.savefig(OUT / "funnel.png", dpi=200)

fig, ax = plt.subplots(figsize=(8, 3), facecolor=CREAM)
ax.set_facecolor(CREAM)
rows = [("Conversion", conv, true_conv / conv["control"]), ("Revenue per visitor", rpv, gmv_impact["true_rel"]),
        ("Average order value", aov, None), ("30-day repeat rate", repeat, None)]
for i, (name, r, truth) in enumerate(rows):
    lo, hi = r["rel_ci"]
    color = ALOE if lo > 0 else ("#e5484d" if hi < 0 else SHADE)
    ax.plot([lo * 100, hi * 100], [i, i], color=color, lw=6, solid_capstyle="round")
    ax.plot(r["rel"] * 100, i, "o", color=INK)
    if truth is not None:
        ax.plot(truth * 100, i, "|", color=INK, ms=14, mew=2)
ax.axvline(0, color=INK, lw=1)
ax.set_yticks(range(len(rows)), [r[0] for r in rows])
ax.invert_yaxis()
ax.set_xlabel("Treatment vs control, % change (95% CI). Tick = true simulated effect")
fig.tight_layout()
fig.savefig(OUT / "experiment.png", dpi=200)

print(json.dumps({
    "conversion": [conv["control"], conv["treatment"], conv["diff"], conv["ci"], conv["p"], "true", true_conv],
    "rpv": [rpv["control"], rpv["treatment"], rpv["rel"], rpv["rel_ci"], rpv["p"], rpv["mann_whitney_p"]],
    "aov": [aov["control"], aov["treatment"], aov["rel"], aov["rel_ci"], aov["p"]],
    "repeat": [repeat["control"], repeat["treatment"], repeat["diff"], repeat["ci"], repeat["p"]],
    "srm_p": srm.pvalue, "gmv": gmv_impact, "steps": steps, "segments": seg_summary,
    "timing": dict(timing), "raw": dict(raw), "by_source": by_source,
}, indent=1, default=float))
