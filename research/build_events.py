"""Поиск ралли и расчёт признаков на USDT-M фьючерсах Binance (5m свечи, окт 2025 – сен 2026).

Две таблицы:
  events.parquet  — триггеры «началось движение» (то, что видит трейдер в реальном времени):
                    признаки только из прошлого + исход в будущем (MFE/MAE, кто первым: TP или SL).
  rallies.parquet — все крупные ралли года (zigzag, рост ≥ 20% от локального дна до пика)
                    и когда по ним сработал бы триггер.

Фильтр ликвидности: скользящий объём за 24ч (в USDT) на момент события от 1 до 100 млн.
"""
import glob, os
import numpy as np
import pandas as pd

KDIR = "data/k5m"
BAR = 5 * 60 * 1000
T0 = pd.Timestamp("2025-10-01", tz="UTC").value // 10**6
T1 = pd.Timestamp("2026-10-01", tz="UTC").value // 10**6
N = (T1 - T0) // BAR
DAY = 288           # баров в сутках
VOL_MIN, VOL_MAX = 1e6, 100e6

TRIG_R5 = 0.015      # триггер «самое начало»: свеча 5м закрылась ≥ +1.5% к прошлому закрытию
TRIG_RVOL = 5.0      #   и объём свечи ≥ 5× медианной 5-мин свечи за 7 суток
CONFIRM = 2          # подтверждение: ещё 2 свечи (10 минут) после триггера
COOLDOWN = 4 * 12    # 4ч: после триггера по символу новых не берём
HORIZON = DAY        # исход смотрим 24ч
TP_SL = [(0.05, 0.02), (0.05, 0.03), (0.10, 0.03), (0.10, 0.05), (0.20, 0.05), (0.20, 0.08)]
ZZ_REV = 0.10        # zigzag: разворот 10%
RALLY_MIN = 0.20     # ралли = рост ≥ 20%


def load(sym):
    df = pd.read_parquet(f"{KDIR}/{sym}.parquet")
    idx = ((df.open_time.values - T0) // BAR).astype(np.int64)
    ok = (idx >= 0) & (idx < N)
    df, idx = df[ok], idx[ok]
    a = {}
    c = np.full(N, np.nan, np.float64); c[idx] = df.c.values
    first = idx.min() if len(idx) else N
    last = idx.max() if len(idx) else -1
    cf = pd.Series(c).ffill().values                    # закрытие с протяжкой через дыры
    for k in ("o", "h", "l"):
        x = np.full(N, np.nan); x[idx] = df[k].values
        a[k] = np.where(np.isnan(x), cf, x)
    a["c"] = cf
    for k in ("qv", "tb_qv", "trades"):
        x = np.zeros(N); x[idx] = df[k].values
        a[k] = x
    a["alive"] = np.zeros(N, bool); a["alive"][first:last + 1] = True
    a["first"] = first
    return a


def rsum(x, w):
    cs = np.concatenate([[0.0], np.cumsum(x)])
    out = np.full(len(x), np.nan)
    out[w - 1:] = cs[w:] - cs[:-w]
    return out


def ret(c, w):
    out = np.full(len(c), np.nan)
    out[w:] = c[w:] / c[:-w] - 1
    return out


def rmax(x, w):
    return pd.Series(x).rolling(w, min_periods=1).max().values


def rmin(x, w):
    return pd.Series(x).rolling(w, min_periods=1).min().values


def rmed(x, w):
    return pd.Series(x).rolling(w, min_periods=w // 2).median().values


def first_touch(h, l, entry, i, tp, sl, horizon):
    """1 — сначала TP, -1 — сначала SL (на одной свече считаем SL первым), 0 — ни то ни другое."""
    up, dn = entry * (1 + tp), entry * (1 - sl)
    hh, ll = h[i + 1:i + 1 + horizon], l[i + 1:i + 1 + horizon]
    a = np.argmax(hh >= up) if (hh >= up).any() else 10**9
    b = np.argmax(ll <= dn) if (ll <= dn).any() else 10**9
    if a == b == 10**9: return 0
    return 1 if a < b else -1


def zigzag(h, l, rev):
    """Точки разворота по high/low: [(idx, price, +1 пик / -1 дно)]."""
    pts, up = [], False
    ext_i, ext = 0, l[0]
    for i in range(1, len(h)):
        if up:
            if h[i] > ext: ext_i, ext = i, h[i]
            elif l[i] <= ext * (1 - rev):
                pts.append((ext_i, ext, 1)); up = False; ext_i, ext = i, l[i]
        else:
            if l[i] < ext: ext_i, ext = i, l[i]
            elif h[i] >= ext * (1 + rev):
                pts.append((ext_i, ext, -1)); up = True; ext_i, ext = i, h[i]
    pts.append((ext_i, ext, 1 if up else -1))
    return pts


def r_outcome(h, l, e, i, stop, hor):
    """Сделка в R: стоп = stop (минимум свечи-триггера), R = e - stop.
    r_max — сколько R цена прошла вверх ДО выбивания стопа (за 24ч); stopped — выбило ли стоп."""
    R = e - stop
    if R <= 0: return np.nan, np.nan, np.nan
    hh, ll = h[i + 1:i + 1 + hor], l[i + 1:i + 1 + hor]
    hit = np.where(ll <= stop)[0]
    k = hit[0] if len(hit) else hor
    # на свече выбивания считаем, что стоп был раньше хая (консервативно)
    best = hh[:k].max() if k > 0 else e
    return (best - e) / R, float(len(hit) > 0), (k + 1) / 12


def outcome(h, l, c, e, i, prefix, stop=None):
    """Исход сделки, открытой по цене e на закрытии бара i (горизонт 24ч)."""
    hor = min(HORIZON, N - 1 - i)
    hh, ll = h[i + 1:i + 1 + hor], l[i + 1:i + 1 + hor]
    r = {
        "mfe1h": h[i + 1:i + 13].max() / e - 1, "mae1h": l[i + 1:i + 13].min() / e - 1,
        "mfe4h": h[i + 1:i + 49].max() / e - 1, "mae4h": l[i + 1:i + 49].min() / e - 1,
        "mfe24h": hh.max() / e - 1, "mae24h": ll.min() / e - 1,
        "ret1h": c[min(i + 12, N - 1)] / e - 1, "ret4h": c[min(i + 48, N - 1)] / e - 1,
        "ret24h": c[i + hor] / e - 1,
        "mae_before_peak": ll[:np.argmax(hh) + 1].min() / e - 1,
        "peak_h": (int(np.argmax(hh)) + 1) / 12,
    }
    for tp, sl in TP_SL:
        r[f"tt_{int(tp*100)}_{int(sl*100)}"] = first_touch(h, l, e, i, tp, sl, hor)
    # насколько глубоко цена уходила вниз ДО того, как дала +5% (или за 24ч, если не дала)
    hit5 = np.argmax(hh >= e * 1.05) if (hh >= e * 1.05).any() else hor - 1
    r["dd_before5"] = ll[:hit5 + 1].min() / e - 1
    if stop is not None:
        r["rmax"], r["stopped"], r["stop_h"] = r_outcome(h, l, e, i, stop, hor)
        r["risk"] = 1 - stop / e
    return {prefix + k: v for k, v in r.items()}


def symbol_features(sym, a, mkt):
    c, h, l, o = a["c"], a["h"], a["l"], a["o"]
    qv, tb, tr = a["qv"], a["tb_qv"], a["trades"]
    pc = np.roll(c, 1)
    v24 = rsum(qv, DAY)
    r5 = c / pc - 1
    # «нормальная» 5-мин свеча за прошлые 7 суток (до триггера)
    base5 = rmed(np.roll(qv, 1), 7 * DAY)
    base5_tr = rmed(np.roll(tr, 1), 7 * DAY)
    lr = np.log(c / pc); lr[0] = 0
    sd5 = np.roll(pd.Series(lr).rolling(DAY, min_periods=DAY // 2).std().values, 1)  # σ 5-мин доходности за 24ч
    hi30d = rmax(np.roll(h, 1), 30 * DAY)
    hi7d = rmax(np.roll(h, 1), 7 * DAY)
    hi24 = rmax(np.roll(h, 1), DAY)
    rng72_pre = np.roll((rmax(h, 3 * DAY) - rmin(l, 3 * DAY)) / c, 1)
    qv6h_pre = np.roll(rsum(qv, 72), 1)
    v7d_daily = np.roll(rsum(qv, 7 * DAY), 1) / 7
    rvol5 = qv / np.where(base5 > 0, base5, np.nan)

    cand = np.where((r5 >= TRIG_R5) & (rvol5 >= TRIG_RVOL) & a["alive"]
                    & (v24 >= VOL_MIN) & (v24 <= VOL_MAX))[0]
    cand = cand[(cand >= a["first"] + 7 * DAY) & (cand < N - 4)]   # неделя истории для базы
    events, last, trig_hist = [], -10**9, []
    for i in cand:
        if i - last < COOLDOWN: continue
        last = i
        trig_hist.append(i)
        e = c[i]
        rng = h[i] - l[i]
        j = i + CONFIRM   # точка подтверждения
        cq = qv[i + 1:j + 1].sum()
        rec = dict(
            sym=sym, i=i, t=pd.Timestamp(T0 + i * BAR, unit="ms"), price=e,
            # --- первая свеча (что видно сразу)
            r5=r5[i], r5_z=r5[i] / sd5[i] if sd5[i] > 0 else np.nan,
            rvol5=rvol5[i], rtrades5=tr[i] / base5_tr[i] if base5_tr[i] > 0 else np.nan,
            ats_ratio=(qv[i] / max(tr[i], 1)) / (base5[i] / base5_tr[i]) if base5_tr[i] > 0 and base5[i] > 0 else np.nan,
            taker5=tb[i] / qv[i] if qv[i] > 0 else np.nan,
            clv=(c[i] - l[i]) / rng if rng > 0 else 0.5,
            upper_wick=(h[i] - max(c[i], o[i])) / rng if rng > 0 else 0,
            gap_open=o[i] / pc[i] - 1,
            brk24h=float(h[i] > hi24[i]), brk7d=float(h[i] > hi7d[i]),
            # --- что было до (контекст)
            r1h_pre=pc[i] / c[i - 13] - 1, r4h_pre=pc[i] / c[i - 49] - 1,
            r24h_pre=pc[i] / c[i - 1 - DAY] - 1, r7d_pre=pc[i] / c[i - 1 - 7 * DAY] - 1,
            dist_hi30d=pc[i] / hi30d[i] - 1, dist_hi7d=pc[i] / hi7d[i] - 1,
            rng72_pre=rng72_pre[i], sd5_pre=sd5[i],
            quiet6h=qv6h_pre[i] / 72 / base5[i] if base5[i] > 0 else np.nan,   # <1 — «полка», объём ниже обычного
            v24=v24[i], v24_vs_7d=v24[i - 1] / v7d_daily[i] if v7d_daily[i] > 0 else np.nan,
            age_days=(i - a["first"]) / DAY,
            prev_trig_7d=sum(1 for k in trig_hist[:-1] if i - k <= 7 * DAY),
            hour=(i % DAY) // 12, dow=pd.Timestamp(T0 + i * BAR, unit="ms").dayofweek,
            btc_r1h=mkt["btc_r1h"][i], btc_r24h=mkt["btc_r24h"][i], breadth=mkt["breadth"][i],
            # --- подтверждение: следующие CONFIRM свечей (решение через 10 мин)
            c_ret=c[j] / e - 1,
            c_pull=l[i + 1:j + 1].min() / e - 1,
            c_newhigh=float(h[i + 1:j + 1].max() > h[i]),
            c_vol=cq / qv[i] if qv[i] > 0 else np.nan,
            c_rvol=cq / CONFIRM / base5[i] if base5[i] > 0 else np.nan,
            c_taker=tb[i + 1:j + 1].sum() / cq if cq > 0 else np.nan,
            c_green=float((c[i + 1:j + 1] > o[i + 1:j + 1]).sum()),
            sl_bar=l[i] / e - 1,
        )
        stop_a = l[i] * 0.998                              # стоп под минимум свечи-триггера (−0.2% буфер)
        stop_b = min(l[i], l[i + 1:j + 1].min()) * 0.998
        rec.update(outcome(h, l, c, e, i, "a_", stop_a))         # вход сразу на закрытии свечи-триггера
        rec.update(outcome(h, l, c, c[j], j, "b_", stop_b))      # вход после подтверждения
        rec["b_sl_bar"] = l[i] / c[j] - 1
        events.append(rec)

    # --- все крупные ралли года
    rallies = []
    alive = np.where(a["alive"])[0]
    s, f = alive[0], alive[-1] + 1
    pts = zigzag(h[s:f], l[s:f], ZZ_REV)
    for (i0, p0, k0), (i1, p1, k1) in zip(pts, pts[1:]):
        if k0 != -1 or p1 / p0 - 1 < RALLY_MIN: continue
        i0 += s; i1 += s
        vstart = v24[i0]
        if not (VOL_MIN <= vstart <= VOL_MAX) or i0 < a["first"] + 7 * DAY: continue
        # когда сработал триггер внутри ралли
        trg = [ev for ev in events if i0 - 12 <= ev["i"] <= i1]
        tri = trg[0]["i"] if trg else None
        rallies.append(dict(
            sym=sym, t0=pd.Timestamp(T0 + i0 * BAR, unit="ms"), gain=p1 / p0 - 1,
            dur_h=(i1 - i0) / 12, v24_start=vstart,
            rvol_peak=np.nanmax(qv[i0:i1 + 1]) / base5[i0] if base5[i0] > 0 else np.nan,
            first30=c[min(i0 + 6, N - 1)] / p0 - 1, first2h=c[min(i0 + 24, N - 1)] / p0 - 1,
            taker_rally=tb[i0:i1 + 1].sum() / max(qv[i0:i1 + 1].sum(), 1),
            n_trig=len(trg), trig_lag_h=(tri - i0) / 12 if tri is not None else np.nan,
            gain_left=p1 / c[tri] - 1 if tri is not None else np.nan,
            gain_at_trig=c[tri] / p0 - 1 if tri is not None else np.nan,
            retrace_after=(l[i1:min(i1 + DAY, N)].min() / p1 - 1),
            pre_rng72=(h[max(i0 - 3 * DAY, 0):i0].max() - l[max(i0 - 3 * DAY, 0):i0].min()) / p0 if i0 > 0 else np.nan,
        ))
    return events, rallies


def market_context(syms):
    btc = load("BTCUSDT")
    mkt = {"btc_r1h": ret(btc["c"], 12), "btc_r24h": ret(btc["c"], DAY)}
    cnt = np.zeros(N); tot = np.zeros(N)
    for s in syms:
        a = load(s)
        r = ret(a["c"], 12)
        ok = a["alive"] & ~np.isnan(r)
        tot += ok; cnt += ok & (r >= 0.03)
    mkt["breadth"] = cnt / np.maximum(tot, 1)   # доля монет с +3% за час
    return mkt


if __name__ == "__main__":
    syms = sorted(os.path.basename(p)[:-8] for p in glob.glob(f"{KDIR}/*.parquet"))
    print("symbols", len(syms))
    mkt = market_context(syms)
    E, R = [], []
    for n, s in enumerate(syms):
        try:
            a = load(s)
            if a["alive"].sum() < 8 * DAY: continue
            e, r = symbol_features(s, a, mkt)
            E += e; R += r
        except Exception as ex:
            print("ERR", s, ex)
        if n % 100 == 0: print(n, s, len(E), len(R), flush=True)
    pd.DataFrame(E).to_parquet("data/events.parquet", index=False)
    pd.DataFrame(R).to_parquet("data/rallies.parquet", index=False)
    print("events", len(E), "rallies", len(R))
