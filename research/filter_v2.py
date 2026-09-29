"""Best filter for the 4h-low sweep LONG ('vynos'), tuned on TRAIN (ot < 2026-05-01) of ev_year.parquet.
Outcome column: r_0.5_2.0 (unchanged exits: TP=+0.5w, SL=-2w, hold 48, fee 0.1%)."""
import numpy as np
RCOL = "r_0.5_2.0"
def keep(ev):
    base = (ev.w >= 0.015) & (ev.w < 0.10) & (ev.hour >= 12) & (ev.ret12 > -2) & (ev.age >= 2016)
    return (base
            & (ev.w / ev.atrp >= 4)      # bounce >= 4 ATR% (strong reclaim relative to recent volatility)
            & (ev.depth > 0.3)           # real sweep: >= 0.3 ATR below the 4h low
            & (ev.qv24 >= 1e6))          # point-in-time liquidity floor: >= $1M quote volume last 24h
