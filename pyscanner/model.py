"""
Модель и признаки — точный перенос из исследования (см. RESEARCH.md в корне репо).

Обучена на 833 USDT-M фьючерсах Binance, год 15-минутных свечей.
Шлюз "тихая полка" подобран перебором 480 комбинаций, модель ранжирует
внутри него: P(+8% за 2 часа), AUC out-of-time 0.868.

Эти же коэффициенты используются в shelf-scanner.html (JS-версия) —
портированы отсюда без изменений, числа совпадают побитово.
"""
import math
from collections import deque

# ---------------------------------------------------------------- модель ----
L = 48  # окно полки = 48 баров по 15м = 12 часов

MODEL_INTERCEPT = -2.433694839477539
MODEL_FEATS = [
    # имя              mu          sd         вес
    ("log_rvol",      4.32982,  0.55945,  +0.0650),
    ("log_rvol96",    4.28831,  0.64636,  -0.2019),
    ("log_rexp",      2.17521,  0.53696,  +0.4358),
    ("brk_n",         1.16106,  2.47420,  +0.1197),
    ("clv",           0.65467,  0.23294,  +0.0951),
    ("body",          0.52133,  0.26087,  -0.0212),
    ("upwick",        0.34521,  0.23287,  -0.0871),
    ("taker",         0.52535,  0.07316,  +0.0323),
    ("log_sw",       -2.92045,  0.75510,  +0.0142),
    ("log_satr",     -5.09370,  0.66143,  +0.7204),
    ("spos",          0.69805,  0.27359,  +0.0699),
    ("sdrift_abs",    0.04097,  0.03444,  +0.0308),
    ("log_sq",       -0.04055,  0.51607,  +0.0899),
    ("log_shelf_age", 1.99516,  2.23495,  +0.0070),
    ("log_qv24",      6.59021,  0.43651,  -0.1516),
    ("log_qv24_mult", 0.14442,  0.80870,  +0.0397),
    ("log_pre3",      1.91825,  1.52637,  -0.0160),
    ("log_qv_accel",  2.09684,  1.68371,  -0.0973),
    ("log_ats",       0.81899,  0.43524,  +0.0470),
    ("log_trd",       3.44577,  0.74384,  +0.1430),
    ("consec_up",     2.74991,  1.83318,  -0.0121),
    ("pos30",         0.40847,  0.25374,  -0.1313),
    ("log_natr",     -4.50658,  0.70553,  +0.5035),
    ("ret_n",         2.61464,  2.11791,  -0.1546),
    ("ret3_n",        3.69946,  2.50432,  +0.3020),
    ("btc_ret6",      0.00046,  0.00691,  -0.0872),
    ("hour_sin",     -0.00844,  0.64749,  +0.0799),
    ("hour_cos",     -0.17459,  0.74183,  -0.0086),
    ("log_bars",      9.14093,  1.03039,  +0.0755),
]

# калибровка score -> вероятность (out-of-time, 69 отложенных дней)
CALIB = [
    dict(t=0.00, day=51.6, p5=0.1423, p10=0.0635, p20=0.0191, p40=0.0028, rec=0.479),
    dict(t=0.05, day=21.8, p5=0.2947, p10=0.1477, p20=0.0453, p40=0.0067, rec=0.479),
    dict(t=0.10, day=13.3, p5=0.3811, p10=0.2075, p20=0.0655, p40=0.0109, rec=0.479),
    dict(t=0.15, day=8.0,  p5=0.4462, p10=0.2617, p20=0.0892, p40=0.0183, rec=0.464),
    dict(t=0.20, day=5.4,  p5=0.4806, p10=0.2866, p20=0.1075, p40=0.0239, rec=0.429),
    dict(t=0.25, day=3.5,  p5=0.5023, p10=0.3180, p20=0.1290, p40=0.0369, rec=0.393),
    dict(t=0.30, day=2.4,  p5=0.5099, p10=0.3510, p20=0.1589, p40=0.0530, rec=0.357),
    dict(t=0.40, day=1.2,  p5=0.5890, p10=0.4521, p20=0.1918, p40=0.0685, rec=0.236),
    dict(t=0.50, day=0.6,  p5=0.6667, p10=0.5278, p20=0.1944, p40=0.0278, rec=0.150),
]
BASE = dict(p5=0.1423, p10=0.0635, p20=0.0191, p40=0.0028)

DEFAULTS = dict(
    maxPairs=600,       # потолок вотчлиста: 600 покрывает полосу целиком
    minQv24=1e6,        # полоса, на которой обучалась и калибровалась модель
    maxQv24=3e8,
    minScore=0.10,      # ~13 сигналов/сутки, P(+10% за 2ч)=20.8%, recall не падает
    maxWidth=0.15,      # ширина полки за 12ч
    maxSatr=0.018,      # ATR полки <= 1.8% за бар — это и есть «тихая»
    minRvol=40,         # объём бара >= 40 медиан полки
    minRexp=1.5,        # размах бара >= 1.5 ATR полки
    minBrk=-0.04,       # ловим ещё на подходе к потолку полки
    maxDrift=0.25,      # полка не в тренде
    maxChase=0.25,      # выше — уже уехало без вас
    intrabar=True,      # оценивать незакрытый 15м бар (форминг-бар по kline-стриму)
    cooldownMin=90,     # не повторять по той же паре
    muteHours="",       # на финальном шлюзе эффект часа исчез — см. RESEARCH.md
    tgToken="",
    tgChat="",
    tgOn=False,
)


def clamp(x, a, b):
    return a if x < a else (b if x > b else x)


def median(xs):
    n = len(xs)
    if n == 0:
        return float("nan")
    ys = sorted(xs)
    m = n // 2
    return ys[m] if n % 2 else (ys[m - 1] + ys[m]) / 2.0


def true_range(bar, prev):
    if prev is None:
        return bar["h"] - bar["l"]
    return max(bar["h"] - bar["l"], abs(bar["h"] - prev["c"]), abs(bar["l"] - prev["c"]))


# ---------------------------------------------------------- признаки ----
def compute_features(sym_state, bars):
    """bars: list-like (индексируемый) закрытых 15м баров, последний = оцениваемый.
    Каждый бар — dict {t,o,h,l,c,qv,n,tb}. Возвращает dict признаков или None."""
    n = len(bars)
    if n < 200:
        return None
    i = n - 1
    cur, prev = bars[i], bars[i - 1]
    if cur is None or prev is None or not (cur["c"] > 0):
        return None

    # --- окно полки: бары [i-L, i-1] ---
    hi, lo, sum_c, sum_tr = -math.inf, math.inf, 0.0, 0.0
    qv_l = []
    for k in range(i - L, i):
        b = bars[k]
        if b is None:
            return None
        if b["h"] > hi:
            hi = b["h"]
        if b["l"] < lo:
            lo = b["l"]
        sum_c += b["c"]
        qv_l.append(b["qv"])
        sum_tr += true_range(b, bars[k - 1] if k - 1 >= 0 else None)

    mid = (hi + lo) / 2
    mean_c = sum_c / L
    atr_l = sum_tr / L
    med_qv_l = median(qv_l)
    sw = (hi - lo) / mid if mid else float("nan")
    satr = atr_l / mean_c if mean_c else float("nan")
    spos = (prev["c"] - lo) / (hi - lo) if (hi - lo) > 0 else 0.5
    brk = cur["c"] / hi - 1 if hi else float("nan")
    rvol = cur["qv"] / med_qv_l if med_qv_l and med_qv_l > 0 else float("nan")
    rexp = (cur["h"] - cur["l"]) / atr_l if atr_l > 0 else float("nan")
    sdrift = (prev["c"] / bars[i - L]["c"] - 1) if bars[i - L] else 0.0

    # sq: медиана объёма полки против медианы за min(4L,192) баров
    span4 = min(4 * L, 192)
    q4 = [bars[k]["qv"] for k in range(max(0, i - span4), i) if bars[k] is not None]
    med4 = median(q4)
    sq = med_qv_l / med4 if med4 and med4 > 0 else float("nan")

    # 96-барные базы
    qv96, n96, ats96 = [], [], []
    for k in range(max(0, i - 96), i):
        b = bars[k]
        if b is None:
            continue
        qv96.append(b["qv"])
        n96.append(b["n"])
        if b["n"] > 0:
            ats96.append(b["qv"] / b["n"])
    med_qv96 = median(qv96)
    med_n96 = median(n96)
    med_ats = median(ats96)
    rvol96 = cur["qv"] / med_qv96 if med_qv96 and med_qv96 > 0 else float("nan")
    trd_mult = cur["n"] / med_n96 if med_n96 and med_n96 > 0 else float("nan")
    ats_cur = cur["qv"] / cur["n"] if cur["n"] > 0 else float("nan")
    ats_mult = (ats_cur / med_ats) if (med_ats and med_ats > 0 and math.isfinite(ats_cur)) else float("nan")

    # предвзрывное накопление: 3 бара до триггера
    pre3 = (bars[i - 1]["qv"] + bars[i - 2]["qv"] + bars[i - 3]["qv"]) / 3
    pre3_mult = pre3 / med_qv96 if med_qv96 and med_qv96 > 0 else float("nan")
    qv_accel = cur["qv"] / prev["qv"] if prev["qv"] > 0 else float("nan")

    # natr(14) включая текущий бар
    s14 = 0.0
    for k in range(i - 13, i + 1):
        s14 += true_range(bars[k], bars[k - 1] if k - 1 >= 0 else None)
    natr = (s14 / 14) / cur["c"]

    # возраст полки: подряд идущие бары с размахом <= 1.8%
    age = 0
    for k in range(i, 0, -1):
        b = bars[k - 1]
        if b is None or not (b["c"] > 0):
            break
        if (b["h"] - b["l"]) / b["c"] <= 0.018:
            age += 1
        else:
            break

    # форма бара
    rng_bar = cur["h"] - cur["l"]
    clv = (cur["c"] - cur["l"]) / rng_bar if rng_bar > 0 else 0.5
    body = abs(cur["c"] - cur["o"]) / rng_bar if rng_bar > 0 else 0.0
    upwick = (cur["h"] - max(cur["c"], cur["o"])) / rng_bar if rng_bar > 0 else 0.0
    taker = cur["tb"] / cur["qv"] if cur["qv"] > 0 else 0.5
    ret = cur["c"] / prev["c"] - 1 if prev["c"] > 0 else 0.0
    ret3 = cur["c"] / bars[i - 3]["c"] - 1 if bars[i - 3] else 0.0
    consec_up = 0
    for k in range(i, 0, -1):
        if bars[k]["c"] > bars[k - 1]["c"]:
            consec_up += 1
        else:
            break

    # дневной контекст (из отдельно загруженных дневных свечей)
    pos30, qv24_mult = 0.5, 1.0
    daily = sym_state.get("daily")
    if daily and len(daily) >= 10:
        dh, dl = -math.inf, math.inf
        dq = []
        for d in daily:
            if d["h"] > dh:
                dh = d["h"]
            if d["l"] < dl:
                dl = d["l"]
            dq.append(d["qv"])
        if dh > dl:
            pos30 = clamp((prev["c"] - dl) / (dh - dl), 0, 1)
        mq = median(dq)
        qv24 = sym_state.get("qv24", 0)
        if mq and mq > 0 and qv24 > 0:
            qv24_mult = qv24 / mq

    # режим рынка (BTC)
    btc_ret6 = 0.0
    btc_bars = sym_state.get("_btc15")
    if btc_bars is not None and len(btc_bars) >= 7:
        m = len(btc_bars) - 1
        b6 = btc_bars[m - 6]
        if b6 and b6["c"] > 0:
            btc_ret6 = btc_bars[m]["c"] / b6["c"] - 1

    import datetime
    hour = datetime.datetime.utcfromtimestamp(cur["t"] / 1000).hour
    onboard = sym_state.get("onboard", 0)
    bars_listed = clamp((cur["t"] - onboard) / 900000, 0, 40000) if onboard else 20000

    sym_state["cache"] = dict(med_qv_l=med_qv_l, hi=hi, atr_l=atr_l)
    return dict(
        sw=sw, satr=satr, spos=spos, brk=brk, rvol=rvol, rexp=rexp, sdrift=sdrift, sq=sq,
        rvol96=rvol96, trd_mult=trd_mult, ats_mult=ats_mult, pre3_mult=pre3_mult,
        qv_accel=qv_accel, natr=natr, age=age, clv=clv, body=body, upwick=upwick,
        taker=taker, ret=ret, ret3=ret3, consec_up=consec_up, pos30=pos30,
        qv24_mult=qv24_mult, btc_ret6=btc_ret6, hour=hour, bars_listed=bars_listed,
        shelf_hi=hi, shelf_lo=lo, price=cur["c"], bar_time=cur["t"],
    )


def feature_vector(f, qv24):
    natr_c = clamp(f["natr"], 1e-4, 1e9) if math.isfinite(f["natr"]) else 1e-4
    h = 2 * math.pi * f["hour"] / 24
    return {
        "log_rvol":       math.log1p(clamp(f["rvol"], 0, 500)) if math.isfinite(f["rvol"]) else float("nan"),
        "log_rvol96":     math.log1p(clamp(f["rvol96"], 0, 500)) if math.isfinite(f["rvol96"]) else float("nan"),
        "log_rexp":       math.log1p(clamp(f["rexp"], 0, 80)) if math.isfinite(f["rexp"]) else float("nan"),
        "brk_n":          clamp(f["brk"] / natr_c, -20, 60) if math.isfinite(f["brk"]) else float("nan"),
        "clv":            f["clv"],
        "body":           f["body"],
        "upwick":         f["upwick"],
        "taker":          f["taker"] if math.isfinite(f["taker"]) else 0.5,
        "log_sw":         math.log(clamp(f["sw"], 1e-4, 3)) if math.isfinite(f["sw"]) else float("nan"),
        "log_satr":       math.log(clamp(f["satr"], 1e-5, 1)) if math.isfinite(f["satr"]) else float("nan"),
        "spos":           clamp(f["spos"], -1, 2),
        "sdrift_abs":     clamp(abs(f["sdrift"]), 0, 2),
        "log_sq":         math.log(clamp(f["sq"], 1e-3, 50)) if math.isfinite(f["sq"]) else float("nan"),
        "log_shelf_age":  math.log1p(clamp(f["age"], 0, 600)),
        "log_qv24":       math.log10(clamp(qv24 or 1e4, 1e4, 1e11)),
        "log_qv24_mult":  math.log(clamp(f["qv24_mult"], 1e-2, 100)),
        "log_pre3":       math.log(clamp(f["pre3_mult"], 1e-2, 200)) if math.isfinite(f["pre3_mult"]) else float("nan"),
        "log_qv_accel":   math.log(clamp(f["qv_accel"], 1e-2, 300)) if math.isfinite(f["qv_accel"]) else float("nan"),
        "log_ats":        math.log(clamp(f["ats_mult"], 1e-2, 100)) if math.isfinite(f["ats_mult"]) else float("nan"),
        "log_trd":        math.log(clamp(f["trd_mult"], 1e-2, 300)) if math.isfinite(f["trd_mult"]) else float("nan"),
        "consec_up":      clamp(f["consec_up"], 0, 10),
        "pos30":          clamp(f["pos30"], 0, 1),
        "log_natr":       math.log(clamp(f["natr"], 1e-5, 0.5)) if math.isfinite(f["natr"]) else float("nan"),
        "ret_n":          clamp(f["ret"] / natr_c, -20, 60),
        "ret3_n":         clamp(f["ret3"] / natr_c, -30, 90),
        "btc_ret6":       clamp(f["btc_ret6"], -0.15, 0.15),
        "hour_sin":       math.sin(h),
        "hour_cos":       math.cos(h),
        "log_bars":       math.log1p(clamp(f["bars_listed"], 0, 40000)),
    }


def score_of(f, qv24):
    v = feature_vector(f, qv24)
    z = MODEL_INTERCEPT
    for name, mu, sd, w in MODEL_FEATS:
        x = v.get(name)
        if x is None or not math.isfinite(x):
            x = mu  # как fillna(0) после нормировки
        z += w * ((x - mu) / (sd or 1))
    return 1.0 / (1.0 + math.exp(-z))


def passes_gate(f, qv24, cfg):
    if not math.isfinite(f["sw"]) or not math.isfinite(f["rvol"]):
        return False
    if f["sw"] > cfg["maxWidth"]:
        return False
    if not (f["satr"] <= cfg["maxSatr"]):
        return False
    if f["rvol"] < cfg["minRvol"]:
        return False
    if f["rexp"] < cfg["minRexp"]:
        return False
    if f["brk"] < cfg["minBrk"]:
        return False
    if f["brk"] > cfg["maxChase"]:
        return False
    if abs(f["sdrift"]) > cfg["maxDrift"]:
        return False
    if not (f["ret"] > 0):
        return False
    if not (f["satr"] > 0):
        return False
    if qv24 < cfg["minQv24"] or qv24 > cfg["maxQv24"]:
        return False
    return True


def explain(f, score):
    out = []
    if f["rvol"] >= 30:
        out.append(f"объём ×{f['rvol']:.0f} к медиане полки")
    elif f["rvol"] >= 10:
        out.append(f"объём ×{f['rvol']:.0f}")
    else:
        out.append(f"объём ×{f['rvol']:.1f}")
    out.append(f"полка {f['sw']*100:.1f}% / 12ч")
    if f["age"] >= 20:
        out.append(f"стоит {f['age']} баров ({f['age']/4:.1f}ч)")
    if f["satr"] <= 0.004:
        out.append("очень тихая полка")
    if f["rexp"] >= 4:
        out.append(f"размах ×{f['rexp']:.1f} ATR")
    if f["brk"] >= 0.005:
        out.append(f"пробой +{f['brk']*100:.1f}% над потолком")
    elif f["brk"] >= 0:
        out.append("на потолке полки")
    else:
        out.append("подходит к потолку")
    if f["pre3_mult"] >= 3:
        out.append(f"подготовка: объём ×{f['pre3_mult']:.1f} за 45м до бара")
    if f["taker"] >= 0.65:
        out.append(f"тейкер-покупки {round(f['taker']*100)}%")
    if f["rvol96"] >= 8 and f["rvol"] / max(f["rvol96"], 1e-9) < 1.5:
        out.append("⚠ объём уже был поднят сутки")
    if f["pos30"] <= 0.25:
        out.append("низ 30-дневного диапазона")
    if f["btc_ret6"] <= -0.005:
        out.append("BTC не растёт — ротация в альты")
    return out


def prob_for(score):
    lo, hi = CALIB[0], CALIB[-1]
    if score <= lo["t"]:
        return lo
    if score >= hi["t"]:
        return hi
    for a, b in zip(CALIB, CALIB[1:]):
        if a["t"] <= score <= b["t"]:
            u = (score - a["t"]) / (b["t"] - a["t"])
            return {k: a[k] + (b[k] - a[k]) * u for k in ("p5", "p10", "p20", "p40")}
    return hi
