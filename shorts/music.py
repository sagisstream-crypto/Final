"""Процедурная эмоциональная музыка: струнный пэд, бас, арпеджио-колокольчики,
хор после кульминации, райзер и удары перед/на кульминации, реверберация."""
import numpy as np
from scipy.signal import butter, sosfilt, fftconvolve

SR = 44100


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def _env(n, attack, release, sr=SR):
    e = np.ones(n)
    a = min(int(attack * sr), n)
    r = min(int(release * sr), n - a)
    if a:
        e[:a] = np.linspace(0, 1, a) ** 1.5
    if r:
        e[n - r:] *= np.linspace(1, 0, r) ** 1.5
    return e


def _add(buf, start, sig):
    s = int(start * SR)
    if s >= len(buf):
        return
    e = min(len(buf), s + len(sig))
    buf[s:e] += sig[: e - s]


def pad_note(f, dur, rng, harmonics=7, detune=(-7, 0, 7), vib=0.0):
    t = np.arange(int(dur * SR)) / SR
    out = np.zeros_like(t)
    for c in detune:
        fc = f * 2 ** (c / 1200)
        ph = 2 * np.pi * fc * t
        if vib:
            ph += vib * np.sin(2 * np.pi * 5.2 * t + rng.random() * 6) * 2 * np.pi * fc / (5.2 * 2 * np.pi) * 0.004
        for h in range(1, harmonics + 1):
            out += np.sin(h * ph + rng.random() * 6.28) / h ** 1.4
    return out / (len(detune) * 2.2)


def bell(f, dur=2.5):
    t = np.arange(int(dur * SR)) / SR
    s = (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 3)
         + 0.12 * np.sin(2 * np.pi * 3.01 * f * t) * np.exp(-t * 6))
    a = np.minimum(1, t / 0.004)
    return s * a * np.exp(-t * 2.2)


def boom(dur=3.0):
    t = np.arange(int(dur * SR)) / SR
    f = 38 + 60 * np.exp(-t * 9)
    ph = 2 * np.pi * np.cumsum(f) / SR
    s = np.sin(ph) * np.exp(-t * 2.3)
    rng = np.random.default_rng(1)
    n = rng.standard_normal(len(t)) * np.exp(-t * 18) * 0.35
    n = sosfilt(butter(2, 900, "lp", fs=SR, output="sos"), n)
    return s + n


def riser(dur, rng):
    n = rng.standard_normal(int(dur * SR))
    t = np.arange(len(n)) / SR
    out = np.zeros_like(n)
    # сдвигаем полосу вверх кусками
    segs = 12
    L = len(n) // segs
    for i in range(segs):
        fc = 300 * 2 ** (i * 4 / segs)
        sos = butter(2, [fc * 0.7, fc * 1.4], "bp", fs=SR, output="sos")
        seg = sosfilt(sos, n[i * L:(i + 1) * L + 2000])
        w = np.hanning(len(seg))
        out[i * L:i * L + len(seg)] += (seg * w)[: len(out) - i * L]
    return out * (t / dur) ** 2.5


def reverb(x, rng, secs=3.2, decay=0.85):
    n = int(secs * SR)
    t = np.arange(n) / SR
    ir = rng.standard_normal(n) * np.exp(-t / decay * 3)
    ir = sosfilt(butter(1, 5000, "lp", fs=SR, output="sos"), ir)
    ir /= np.sqrt(np.sum(ir ** 2))
    return fftconvolve(x, ir)[: len(x)]


def compose(root, prog, climax_t, dur=30.0, seed=0, bpm=70, arp_oct=2, brightness=1800, pulse_from=None):
    """root: MIDI тоника; prog: список аккордов (полутона от тоники);
    climax_t: время кульминации в секундах. Возвращает стерео (N,2)."""
    rng = np.random.default_rng(seed)
    N = int(dur * SR)
    pad = np.zeros(N)
    bass = np.zeros(N)
    arp = np.zeros(N)
    choir = np.zeros(N)
    hits = np.zeros(N)
    cd = dur / len(prog)
    eighth = 60 / bpm / 2
    for i, ch in enumerate(prog):
        t0 = i * cd
        notes = [root + n for n in ch]
        for m in notes:
            s = pad_note(midi_hz(m), cd + 1.8, rng) * _env(int((cd + 1.8) * SR), 1.2, 1.8)
            _add(pad, t0, s * 0.33)
        b = pad_note(midi_hz(root + ch[0] - 12), cd + 1.2, rng, harmonics=4, detune=(0,))
        _add(bass, t0, b * _env(len(b), 0.6, 1.2) * 0.55)
        # арпеджио с 2-й секунды
        pattern = [0, 1, 2, 1, 2, 3, 2, 1]
        seq = sorted(notes) + [sorted(notes)[0] + 12]
        k = 0
        tt = t0
        while tt < t0 + cd - 1e-6:
            if tt >= 1.6:
                m = seq[pattern[k % 8] % len(seq)] + 12 * (arp_oct - 1)
                vel = 0.55 + 0.25 * rng.random()
                if tt > climax_t:
                    vel *= 1.25
                _add(arp, tt + rng.normal(0, 0.006), bell(midi_hz(m)) * vel * 0.16)
            k += 1
            tt += eighth if tt < climax_t else eighth / 2
        # хор после кульминации
        if t0 + cd > climax_t - 0.5:
            for m in notes:
                s = pad_note(midi_hz(m + 12), cd + 1.8, rng, harmonics=4, vib=1.0)
                st = max(t0, climax_t - 0.3)
                s = s[: int((t0 + cd + 1.8 - st) * SR)]
                _add(choir, st, s * _env(len(s), 0.9, 1.8) * 0.22)
    # удары
    _add(hits, 0.0, boom() * 0.5)
    _add(hits, climax_t, boom(4) * 1.1)
    for k in range(1, 4):
        if climax_t + k * cd * 0.5 < dur - 2:
            _add(hits, climax_t + k * cd * 0.5, boom() * 0.45)
    # «сердцебиение» перед кульминацией
    hb = climax_t - 6
    while hb < climax_t - 0.8:
        if hb > 2:
            _add(hits, hb, boom(1.2) * 0.18)
            _add(hits, hb + 0.28, boom(1.0) * 0.12)
        hb += 60 / bpm * 2
    # ровный нарастающий пульс (для мотивационного подъёма)
    if pulse_from is not None:
        beat = 60 / bpm
        tt = pulse_from
        while tt < dur - 2.0:
            post = tt >= climax_t
            if abs(tt - climax_t) > 0.2 and not (climax_t - 6 < tt < climax_t):
                v = 0.32 if post else 0.12 + 0.12 * (tt - pulse_from) / max(1, climax_t - pulse_from)
                _add(hits, tt, boom(1.2) * v)
            tt += beat if not post else beat
    rz = riser(3.0, rng)
    _add(hits, climax_t - 3.0, rz * 0.12)

    pad = sosfilt(butter(4, brightness, "lp", fs=SR, output="sos"), pad)
    choir = sosfilt(butter(2, [300, 1600], "bp", fs=SR, output="sos"), choir)
    t = np.arange(N) / SR
    dyn = np.interp(t, [0, 3, climax_t - 0.2, climax_t + 0.5, dur - 2.5, dur],
                    [0.25, 0.55, 0.8, 1.0, 1.0, 0.0])
    dry = (pad * 1.0 + bass + arp + choir * 1.3) * dyn + hits * np.interp(t, [dur - 1.5, dur], [1, 0], right=0)
    L = dry * 0.7 + reverb(dry, rng) * 0.55
    R = dry * 0.7 + reverb(dry, rng) * 0.55
    st = np.stack([L, R], 1)
    st /= np.max(np.abs(st)) + 1e-9
    return st * 0.9
