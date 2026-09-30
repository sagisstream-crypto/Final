"""Портфельный бэктест mean-reversion стратегий на дневках.

Правила исполнения (без заглядывания в будущее):
  * сигнал считается по закрытию дня t;
  * вход — на открытии t+1 (или лимит-ордером в t+1, если задан limit_atr);
  * выход — по закрытию, когда close > SMA(exit_sma) или прошло max_hold дней;
  * комиссия+проскальзывание cost на каждую сторону.
"""
import numpy as np
import pandas as pd


def rsi(close, n=2):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def atr(h, l, c, n=10):
    pc = c.shift()
    tr = np.maximum(h - l, np.maximum((h - pc).abs(), (l - pc).abs()))
    return tr.rolling(n).mean()


def run(d, tickers, rsi_max=10, trend_sma=200, exit_sma=5, max_hold=10,
        max_pos=10, limit_atr=0.0, stop_atr=None, cost=0.0005,
        start=None, end=None, entry_close=False, regime=False,
        exit_mode="sma"):
    O, H, L, C = (d[k][tickers] for k in ("Open", "High", "Low", "Close"))
    r2 = rsi(C, 2)
    trend = C > C.rolling(trend_sma).mean()
    sma_x = C.rolling(exit_sma).mean()
    a = atr(H, L, C, 10)
    vol_ok = (d["Close"][tickers] * d["Volume"][tickers]).rolling(20).mean() > 2e7
    sig = (r2 < rsi_max) & trend & vol_ok & C.notna()
    if regime:  # торгуем только когда рынок (SPY) выше своей SMA200
        spy = d["Close"]["SPY"]
        sig = sig & (spy > spy.rolling(200).mean()).values[:, None]

    idx = C.index
    if start: idx = idx[idx >= start]
    if end: idx = idx[idx <= end]
    Cv, Ov, Lv, Hv = C.values, O.values, L.values, H.values
    pos_map = {t: i for i, t in enumerate(tickers)}
    rows = {dt: i for i, dt in enumerate(C.index)}

    cash, equity_curve, trades = 1.0, [], []
    open_pos = {}  # ticker -> dict(shares, entry, day, stop)
    pending = []   # (ticker, limit_price, stop_dist) на следующий день

    for dt in idx:
        i = rows[dt]
        # 1) исполнение входов, выбранных вчера
        eq_prev = cash + sum(p["sh"] * Cv[i - 1, pos_map[t]] for t, p in open_pos.items())
        for t, lim, sd in pending:
            if len(open_pos) >= max_pos or t in open_pos:
                continue
            j = pos_map[t]
            o, lo = Ov[i, j], Lv[i, j]
            if np.isnan(o):
                continue
            if lim is None:
                px = o
            elif o <= lim:
                px = o
            elif lo <= lim:
                px = lim
            else:
                continue  # лимит не исполнился
            alloc = min(cash, eq_prev / max_pos)
            if alloc <= 0:
                break
            px_c = px * (1 + cost)
            open_pos[t] = dict(sh=alloc / px_c, entry=px_c, day=i,
                               stop=(px - sd) if sd else None, t0=dt)
            cash -= alloc
        pending = []

        # 2) выходы по закрытию (или по стопу внутри дня)
        for t in list(open_pos):
            p, j = open_pos[t], pos_map[t]
            c = Cv[i, j]
            if np.isnan(c):
                continue
            exit_px = None
            if p["stop"] is not None and Lv[i, j] <= p["stop"] and i > p["day"]:
                exit_px = min(Ov[i, j], p["stop"])
            elif i == p["day"] and p.get("at_close"):
                continue  # вошли на этом закрытии
            elif ((c > sma_x.values[i, j] if exit_mode == "sma" else
                   c > Cv[i - 1, j] if exit_mode == "up" else c * (1 - cost) > p["entry"] * 1.003)
                  or i - p["day"] + 1 >= max_hold):
                exit_px = c
            if exit_px is not None:
                px = exit_px * (1 - cost)
                cash += p["sh"] * px
                trades.append(dict(ticker=t, entry_date=p["t0"], exit_date=dt,
                                   ret=px / p["entry"] - 1, days=i - p["day"] + 1))
                del open_pos[t]

        # 3) новые сигналы на завтра, сортировка по «перепроданности»
        free = max_pos - len(open_pos)
        if free > 0:
            s = sig.values[i]
            cand = [(r2.values[i, j], tickers[j]) for j in np.where(s)[0]
                    if tickers[j] not in open_pos]
            cand.sort()
            for _, t in cand[:free if entry_close else free * 2]:
                j = pos_map[t]
                lim = Cv[i, j] - limit_atr * a.values[i, j] if limit_atr else None
                sd = stop_atr * a.values[i, j] if stop_atr else None
                if entry_close:  # MOC-ордер по закрытию дня сигнала
                    eq_now = cash + sum(p["sh"] * Cv[i, pos_map[u]] for u, p in open_pos.items())
                    alloc = min(cash, eq_now / max_pos)
                    if alloc <= 0 or len(open_pos) >= max_pos:
                        break
                    px_c = Cv[i, j] * (1 + cost)
                    open_pos[t] = dict(sh=alloc / px_c, entry=px_c, day=i, at_close=True,
                                       stop=(Cv[i, j] - sd) if sd else None, t0=dt)
                    cash -= alloc
                else:
                    pending.append((t, lim, sd))

        eq = cash + sum(p["sh"] * Cv[i, pos_map[t]] for t, p in open_pos.items()
                        if not np.isnan(Cv[i, pos_map[t]]))
        equity_curve.append((dt, eq))

    eq = pd.Series(dict(equity_curve))
    return eq, pd.DataFrame(trades)


def stats(eq, tr, bench=None):
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    dr = eq.pct_change().dropna()
    sharpe = dr.mean() / dr.std() * np.sqrt(252)
    w = tr.ret > 0
    pf = tr.ret[w].sum() / -tr.ret[~w].sum()
    out = dict(trades=len(tr), winrate=w.mean(), avg_win=tr.ret[w].mean(),
               avg_loss=tr.ret[~w].mean(), worst=tr.ret.min(), profit_factor=pf,
               avg_days=tr.days.mean(), CAGR=cagr, maxDD=dd, sharpe=sharpe)
    if bench is not None:
        b = bench.loc[eq.index[0]:eq.index[-1]]
        out["bench_CAGR"] = (b.iloc[-1] / b.iloc[0]) ** (1 / yrs) - 1
        out["bench_maxDD"] = (b / b.cummax() - 1).min()
    return out
