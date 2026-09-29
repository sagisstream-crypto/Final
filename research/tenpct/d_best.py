"""d_best: DEEP-NEGATIVE-FUNDING, LOW-VOL SHORT (derivatives positioning) on Binance USDT-M alt perps.

Rule (side = -1, SHORT), evaluated at the close of hour t (lab10 entry convention):
    fr8_t   = last SETTLED funding rate known at t's close, normalised to 8h  (fr * 8 / interval_hours)
    vol24_t = mean over the last 24 hourly bars of ln(high/low)
    signal  = fr8_t < -0.98%  AND  vol24_t < 5%
Interpretation: extreme negative funding is NOT a squeeze setup here; shorts are crowding a coin that keeps bleeding
(OI rising into it makes the short even better). Low realised vol filters out already-squeezing names.

DATA NEEDS (besides lab10 h10/ bars):
  data_extra_d/fund_{SYM}.parquet     monthly fundingRate archive (d_dl.py fund), cols t, ih, fr
  data_extra_d/fundsep_{SYM}.parquet  Sep-2026 funding from www.binance.com/fapi/v1/fundingRate (d_dlsep.py)
CAVEAT: labels ignore funding; shorts PAY the negative funding while holding. Measured mean funding cost ~1.6-1.8% per trade
(EV 2.7-2.9% -> ~1.1% after funding). Median hold ~16h.
Usage: H, side = signal(sym)  -> H = lab10.load('fut', sym) with boolean column 'mask'; trades non-overlapping per symbol.
"""
import numpy as np, pandas as pd, lab10
from d_feat import load_fund
FR_THR, VOL_THR, HR = -0.0098, 0.05, 3600000

def signal(sym):
    H = lab10.load("fut", sym)
    f = load_fund(sym).sort_values("t")
    ot = H.ot.values.astype(np.int64)
    fr8 = np.full(len(H), np.nan)
    if len(f):
        idx = np.searchsorted(f.t.values, ot + HR, side="right") - 1
        ok = idx >= 0; j = np.maximum(idx, 0)
        v = (f.fr.values * 8 / np.clip(f.ih.values, 1, 8))[j]
        stale = (ot + HR - f.t.values[j]) > 9 * HR
        fr8 = np.where(ok & ~stale, v, np.nan)
    vol24 = (np.log(H.h) - np.log(H.l)).rolling(24, min_periods=12).mean().values
    H["fr8"] = fr8; H["vol24"] = vol24
    H["mask"] = (fr8 < FR_THR) & (vol24 < VOL_THR)
    return H, -1

if __name__ == "__main__":
    from d_eval import trades, stats
    parts = []
    for m, s in lab10.all_syms():
        if m != "fut": continue
        H, side = signal(s); H["sym"] = s; parts.append(H[["sym", "ot", "mask", "lab_long", "lab_short", "up_t", "dn_t"]])
    F = pd.concat(parts, ignore_index=True)
    T = trades(F, F["mask"].values, -1)
    print("TRAIN", stats(T[T.ot < lab10.SPLIT])); print("TEST", stats(T[T.ot >= lab10.SPLIT]))
