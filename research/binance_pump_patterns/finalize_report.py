#!/usr/bin/env python3
"""Turns runup_events.partial.csv + symbol_screen.partial.csv into the finished
6-month report: feature_separation.csv (P(REAL>FAIL) per feature, the effect-size
check behind the SCORE weights in ../../../index.html's computeScore()),
conditional_hit_rates.csv (real-rate lift for single/combo rules), and report.html.

Reference copy, kept as-is: hardcodes /home/workdir/artifacts. This is the script
that produced feature_separation.csv/conditional_hit_rates.csv/report.html for the
193-event 6-month set (not the 267-event 12-month one in results_12m/) -- adjust
OUT and the two input filenames for your own paths and dataset before reusing it.
"""
from __future__ import annotations
from pathlib import Path
import json
import numpy as np
import pandas as pd

OUT = Path("/home/workdir/artifacts/binance_6m_results")
OUT.mkdir(parents=True, exist_ok=True)

screen = pd.read_csv(OUT / "symbol_screen.partial.csv")
screen["eligible"] = screen["eligible"].astype(str).str.lower().eq("true")
screen["min_24h_quote"] = pd.to_numeric(screen["min_24h_quote"], errors="coerce")
screen = screen.drop_duplicates("symbol")
screen.to_csv(OUT / "symbol_screen.csv", index=False)

events = pd.read_csv(OUT / "runup_events.partial.csv")
num_cols = [c for c in events.columns if c not in ("symbol", "time_utc", "label")]
for c in num_cols:
    events[c] = pd.to_numeric(events[c], errors="coerce")
events = events.dropna(subset=["time_utc", "symbol", "label"])
events = events[events.label.isin(["REAL_CONTINUATION", "FAILED_OR_PARTIAL"])]
events["time_utc"] = pd.to_datetime(events["time_utc"], utc=True, errors="coerce")
events = events.dropna(subset=["time_utc"]).sort_values(["time_utc", "symbol"])
events.to_csv(OUT / "runup_events.csv", index=False)

processed = 0
pf = OUT / "processed_5m.txt"
if pf.exists():
    processed = len([x for x in pf.read_text().split() if x])

def summarize(e: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for label, g in e.groupby("label"):
        row = {"label": label, "events": int(len(g)), "unique_symbols": int(g.symbol.nunique())}
        cols = [
            "ret5_pct", "range_pct", "body_pct", "close_pos", "volume24_usd", "rvol",
            "vol_accel_1h", "range12_pct", "trades_accel", "taker_buy_share",
            "mfe_30m_pct", "mfe_1h_pct", "mfe_2h_pct", "mfe_4h_pct",
            "mae_30m_pct", "mae_1h_pct", "mae_2h_pct", "mae_4h_pct",
        ]
        for c in cols:
            row[f"{c}_median"] = float(g[c].median())
            row[f"{c}_p25"] = float(g[c].quantile(0.25))
            row[f"{c}_p75"] = float(g[c].quantile(0.75))
        rows.append(row)
    return pd.DataFrame(rows)

summary = summarize(events)
summary.to_csv(OUT / "runup_summary.csv", index=False)
first_only = events.sort_values("time_utc").drop_duplicates("symbol", keep="first")
summarize(first_only).to_csv(OUT / "runup_summary_first_per_symbol.csv", index=False)

feat_cols = [
    "ret5_pct", "range_pct", "body_pct", "close_pos", "rvol", "vol_accel_1h",
    "range12_pct", "trades_accel", "taker_buy_share", "volume24_usd", "quote_volume_5m",
]
real = events[events.label == "REAL_CONTINUATION"]
fail = events[events.label == "FAILED_OR_PARTIAL"]
sep_rows = []
rng = np.random.default_rng(7)
for c in feat_cols:
    a = real[c].dropna()
    b = fail[c].dropna()
    if a.empty or b.empty:
        continue
    aa = a.to_numpy(); bb = b.to_numpy()
    samp_a = rng.choice(aa, size=min(4000, 400 * len(aa)), replace=True)
    samp_b = rng.choice(bb, size=len(samp_a), replace=True)
    p_gt = float((samp_a > samp_b).mean())
    sep_rows.append({
        "feature": c,
        "real_median": float(a.median()),
        "fail_median": float(b.median()),
        "real_p75": float(a.quantile(0.75)),
        "fail_p75": float(b.quantile(0.75)),
        "median_diff": float(a.median() - b.median()),
        "p_real_gt_fail": p_gt,
        "direction": "higher_in_REAL" if a.median() > b.median() else "higher_in_FAIL",
    })
sep = pd.DataFrame(sep_rows).sort_values("p_real_gt_fail", ascending=False)
sep.to_csv(OUT / "feature_separation.csv", index=False)

rules = []
def hit(mask, name):
    sub = events[mask]
    if len(sub) < 12:
        return
    rules.append({
        "rule": name,
        "n": int(len(sub)),
        "real_rate": float((sub.label == "REAL_CONTINUATION").mean()),
        "mfe2h_median": float(sub.mfe_2h_pct.median()),
        "mae2h_median": float(sub.mae_2h_pct.median()),
    })

base_rate = float((events.label == "REAL_CONTINUATION").mean())
hit(events.ret5_pct >= 7, "ret5 >= 7%")
hit(events.ret5_pct >= 10, "ret5 >= 10%")
hit(events.close_pos >= 0.80, "close in top 20% of candle")
hit(events.close_pos >= 0.90, "close in top 10% of candle")
hit(events.body_pct >= 70, "body >= 70% of range")
hit(events.rvol >= 100, "RVOL >= 100")
hit(events.rvol >= 250, "RVOL >= 250")
hit(events.taker_buy_share >= 0.60, "taker_buy_share >= 0.60")
hit(events.taker_buy_share >= 0.70, "taker_buy_share >= 0.70")
hit(events.range12_pct <= 4, "12h range <= 4% (compression)")
hit(events.range12_pct <= 6, "12h range <= 6%")
hit(events.range12_pct >= 8, "12h range >= 8% (already expanded)")
hit((events.ret5_pct >= 7) & (events.close_pos >= 0.80), "ret5>=7 AND close_pos>=0.80")
hit((events.ret5_pct >= 7) & (events.body_pct >= 70), "ret5>=7 AND body>=70")
hit((events.ret5_pct >= 7) & (events.close_pos >= 0.80) & (events.body_pct >= 70), "ret5>=7 AND close_pos>=0.80 AND body>=70")
hit((events.ret5_pct >= 7) & (events.rvol >= 150), "ret5>=7 AND RVOL>=150")
hit(events.vol_accel_1h >= 80, "vol_accel_1h >= 80")
hit(events.vol_accel_1h < 40, "vol_accel_1h < 40")
rules_df = pd.DataFrame(rules)
rules_df["lift_vs_base"] = rules_df["real_rate"] / base_rate if base_rate else np.nan
rules_df = rules_df.sort_values("real_rate", ascending=False)
rules_df.to_csv(OUT / "conditional_hit_rates.csv", index=False)

events.groupby("label")[["mfe_30m_pct","mfe_1h_pct","mfe_2h_pct","mfe_4h_pct"]].median().to_csv(OUT/"continuation_medians.csv")
top = events.sort_values(["mfe_2h_pct","rvol"], ascending=False).head(80)
top.to_csv(OUT / "top_2h_continuations.csv", index=False)

usdt_elig = screen[(screen.eligible) & (screen.symbol.str.endswith("USDT")) & (screen.min_24h_quote > 0)]
meta = {
    "archive_symbols_listed": 1018,
    "symbols_with_1h_history_saved": int(len(screen)),
    "eligible_any_quote": int(screen.eligible.sum()),
    "eligible_usdt_min24h_gt0": int(len(usdt_elig)),
    "symbols_processed_5m": processed,
    "events": int(len(events)),
    "unique_event_symbols": int(events.symbol.nunique()),
    "real_events": int((events.label == "REAL_CONTINUATION").sum()),
    "fail_events": int((events.label == "FAILED_OR_PARTIAL").sum()),
    "real_rate": base_rate,
    "window_effective": "2026-03-18 .. 2026-08-31 UTC",
    "window_planned": "2026-03-18 .. 2026-09-18 UTC",
}
(OUT / "README.txt").write_text("\n".join(f"{k}={v}" for k,v in meta.items())+"\n", encoding="utf-8")
(OUT / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

def fmt_table(df, nd=3):
    return df.to_html(index=False, float_format=lambda v: f"{v:.{nd}f}")

sum_show = ["label","events","unique_symbols","ret5_pct_median","body_pct_median","close_pos_median",
            "rvol_median","vol_accel_1h_median","range12_pct_median","taker_buy_share_median",
            "mfe_2h_pct_median","mae_2h_pct_median","mfe_4h_pct_median"]
score_bits = []
for _, r in sep.iterrows():
    strength = abs(r.p_real_gt_fail - 0.5)
    if strength < 0.05:
        rec = "слабый / почти не использовать"
    elif r.direction == "higher_in_REAL":
        rec = "плюс к SCORE при высоком значении"
    else:
        rec = "плюс к SCORE при НИЗКОМ значении"
    score_bits.append(f"<tr><td>{r.feature}</td><td>{r.real_median:.3f}</td><td>{r.fail_median:.3f}</td><td>{r.p_real_gt_fail:.3f}</td><td>{rec}</td></tr>")

top_cols = ["symbol","time_utc","ret5_pct","rvol","volume24_usd","close_pos","body_pct","range12_pct","taker_buy_share","mfe_2h_pct","mae_2h_pct","label"]
html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Binance 6M Run-up Research</title>
<style>
body{{font-family:Arial,sans-serif;margin:28px;max-width:1200px;color:#111}}
table{{border-collapse:collapse;font-size:12px;margin:12px 0 24px}}
th,td{{border:1px solid #ccc;padding:5px 7px}}
th{{background:#eef}}
.note{{background:#fff6d6;border:1px solid #e0c36a;padding:12px 14px}}
.warn{{background:#ffecec;border:1px solid #e0a0a0;padding:12px 14px}}
code{{background:#f4f4f4;padding:1px 4px}}
</style></head><body>
<h1>Binance USDⓈ-M Futures — исследование выносов</h1>
<p>План: 2026-03-18 → 2026-09-18 UTC. Факт: 2026-03-18 → 2026-08-31 UTC (сентябрьские daily не качались).</p>
<div class="warn"><b>Исследование, не сигнал.</b> REAL_CONTINUATION = сначала +20% за 2ч от open импульсной 5m свечи, без −8% до этого.</div>
<h2>Покрытие</h2>
<ul>
<li>Префиксов архива: 1018</li>
<li>1h история сохранена: {meta['symbols_with_1h_history_saved']}</li>
<li>Когда-либо 24h quote ≤ $2M: {meta['eligible_any_quote']}</li>
<li>USDT и min24h&gt;0: {meta['eligible_usdt_min24h_gt0']}</li>
<li>Разобрано 5m: {meta['symbols_processed_5m']}</li>
<li>Событий: {meta['events']} на {meta['unique_event_symbols']} тикерах</li>
<li>REAL: {meta['real_events']} ({base_rate*100:.1f}%) / FAIL: {meta['fail_events']}</li>
</ul>
<div class="note">Источник: Binance Public Data. Пропуски не заполнялись. Повторные спайки одной монеты завышают вес тикера.</div>
<h2>Сравнение REAL vs FAIL</h2>
{fmt_table(summary[sum_show])}
<h2>Разделение признаков (P(REAL&gt;FAIL))</h2>
{fmt_table(sep)}
<h2>Условная частота REAL, база {base_rate*100:.1f}%</h2>
{fmt_table(rules_df)}
<h2>SCORE 0–100 по этой выборке</h2>
<table><tr><th>Признак</th><th>median REAL</th><th>median FAIL</th><th>P(REAL&gt;FAIL)</th><th>Как использовать</th></tr>
{''.join(score_bits)}</table>
<ol>
<li>Сильные плюсы: close у хая, широкое тело, более крупный 5m ret.</li>
<li>Средний плюс: высокий RVOL, без ставки только на экстремум.</li>
<li>taker_buy_share здесь почти бесполезен (~0.56 оба класса).</li>
<li>vol_accel и trades_accel чаще выше у FAIL — голый взрыв сделок не равен продолжению.</li>
<li>12ч компрессия не главный фильтр: у REAL диапазон даже чуть шире.</li>
</ol>
<h2>Крупнейшие 2h продолжения</h2>
{fmt_table(top[top_cols])}
</body></html>
"""
(OUT / "report.html").write_text(html, encoding="utf-8")
print("OK events", len(events), "real_rate", round(base_rate,4), "screen", len(screen), "p5m", processed)
