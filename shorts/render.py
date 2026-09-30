"""Сборка шортса: озвучка -> музыка -> кадры -> mp4 (1080x1920, 30 fps, 30 c).

python3 render.py 01_dragon            # полный рендер
python3 render.py 01_dragon --preview  # несколько PNG-кадров для проверки
"""
import json
import os
import subprocess
import sys

import imageio_ffmpeg
import numpy as np
import scipy.io.wavfile as wavfile
from PIL import Image

from engine import W, H, FPS, PAD, ease, bloom, VIGNETTE, Captions, Title
from music import compose, SR, reverb
from scenes import SCENES
from stories import STORIES
from tts import synth

FF = imageio_ffmpeg.get_ffmpeg_exe()
DUR = 30.0
VOICE_OFF = 0.8
HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
OUT = os.path.join(HERE, "out")

EVENTS = {
    "01_dragon": {"climax": "треснула", "tear": "заплакал"},
    "02_stars": {"climax": "спустилась", "hug": "обняла"},
    "03_knight": {"climax": "ждала", "helm": "шлем", "sit": "сел"},
    "04_forest": {"believe": "верю", "climax": "засиял"},
    "05_ice": {"open": "открыла", "climax": "вспыхнуло"},
    "06_summit": {"fall": "падала", "storm": "буря", "rise": "вставала", "dawn": "годы", "climax": "раскрылись",
                  "final": "поднимайся"},
}
MUSIC = {
    "01_dragon": dict(root=50, prog=[[0, 3, 7], [-4, 0, 3], [3, 7, 10], [-2, 2, 5]] * 2, bpm=66, seed=1),
    "02_stars": dict(root=53, prog=[[0, 4, 7], [-5, -1, 2], [2, 5, 9], [-2, 2, 5]] * 2, bpm=72, seed=2, brightness=2400),
    "03_knight": dict(root=52, prog=[[0, 3, 7], [5, 8, 12], [-4, 0, 3], [-5, -1, 2]] * 2, bpm=64, seed=3),
    "04_forest": dict(root=55, prog=[[0, 4, 7], [-5, -1, 2], [-3, 0, 4], [-7, -3, 0]] * 2, bpm=70, seed=4, brightness=2200),
    "05_ice": dict(root=59, prog=[[0, 3, 7], [-4, 0, 3], [3, 7, 10], [-2, 2, 5]] * 2, bpm=68, seed=5, arp_oct=3),
    # vi-IV-I-V: грусть -> надежда
    "06_summit": dict(root=55, prog=[[-3, 0, 4], [-7, -3, 0], [0, 4, 7], [-5, -1, 2]] * 3, bpm=72, seed=6,
                      brightness=2400),
}


def decode(path):
    raw = subprocess.run([FF, "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def prepare_voice(story):
    os.makedirs(BUILD, exist_ok=True)
    sid = story["id"]
    mp3, js = os.path.join(BUILD, sid + ".mp3"), os.path.join(BUILD, sid + ".json")
    if os.path.exists(js) and os.path.exists(mp3):
        return json.load(open(js))
    for rate in ("-6%", "-2%", "+2%", "+6%"):
        words = synth(story["text"], story["voice"], mp3, rate=rate, pitch=story["pitch"])
        if words[-1][1] + VOICE_OFF < DUR - 1.4:
            break
    json.dump(words, open(js, "w"), ensure_ascii=False)
    return words


def event_times(sid, words):
    ev = {}
    for k, stem in EVENTS[sid].items():
        for s, e, w in words:
            if stem in w.lower():
                ev[k] = s + VOICE_OFF
                break
    return ev


def make_audio(story, ev):
    sid = story["id"]
    wav = os.path.join(BUILD, sid + ".wav")
    v = decode(os.path.join(BUILD, sid + ".mp3"))
    rng = np.random.default_rng(0)
    v = v / (np.sqrt(np.mean(v[np.abs(v) > 0.01] ** 2)) + 1e-9) * 0.16
    v = v + reverb(v, rng, 1.6, 0.4) * 0.18
    N = int(DUR * SR)
    voice = np.zeros(N)
    s = int(VOICE_OFF * SR)
    voice[s:s + len(v)] = v[: N - s]
    extra = {"pulse_from": ev["dawn"]} if "dawn" in ev else {}
    m = compose(climax_t=ev["climax"], dur=DUR, **MUSIC[sid], **extra)
    # дакинг музыки под голос
    env = np.abs(voice)
    k = int(0.25 * SR)
    env = np.convolve(env, np.ones(k) / k, mode="same")
    env = np.clip(env / (env.max() + 1e-9) * 3, 0, 1)
    duck = 1 - 0.45 * env
    mix = m * 0.32 * duck[:, None] + voice[:, None]
    mix = np.tanh(mix * 1.1) / np.tanh(1.1)
    mix *= 0.93 / np.max(np.abs(mix))
    wavfile.write(wav, SR, (mix * 32767).astype(np.int16))
    return wav


class Renderer:
    def __init__(self, story, words, ev):
        self.scene = SCENES[story["id"]](ev)
        self.ev = ev
        self.caps = Captions(story["text"], words, VOICE_OFF, y=self.scene.caption_y)
        self.title = Title(story["title"])

    def frame(self, t):
        sc = self.scene
        cam = -60 + 120 * (t / DUR)
        base = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        for lay in sc.layers:
            img, speed, yoff = lay[:3]
            if len(lay) > 3:
                a = lay[3](t)
                if a <= 0.004:
                    continue
                if a < 0.996:
                    x0 = int(round(PAD + cam * speed))
                    c = np.asarray(img.crop((x0, 0, x0 + W, img.height))).copy()
                    c[..., 3] = (c[..., 3] * a).astype(np.uint8)
                    base.alpha_composite(Image.fromarray(c, "RGBA"), (0, yoff))
                    continue
            if img.width > W + 2 * PAD:  # туман: медленно плывёт
                x0 = int(PAD + cam * speed + t * 22 * speed)
                base.alpha_composite(img.crop((x0, 0, x0 + W, img.height)), (0, yoff))
            else:
                x0 = int(round(PAD + cam * speed))
                base.alpha_composite(img.crop((x0, 0, x0 + W, H)))
        sc.dynamic(base, t, cam)
        arr = np.asarray(base, np.float32)[..., :3].copy()
        L = np.zeros_like(arr)
        sc.light(L, t, cam)
        arr += L
        arr = sc.grade(arr, t)
        arr = bloom(arr)
        tc = self.ev["climax"]
        z = 1.0 + 0.1 * ease(t / DUR) + (0.035 * np.exp(-(t - tc) * 1.8) if t > tc else 0)
        im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        cx, cy = W / 2, H * 0.55
        cw, ch = W / z, H / z
        cy = min(max(cy, ch / 2), H - ch / 2)
        im = im.resize((W, H), Image.BILINEAR, box=(cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2))
        arr = np.asarray(im, np.float32) * VIGNETTE
        fade = ease(t / 0.8) * (1 - ease((t - (DUR - 1.3)) / 1.2))
        arr *= fade
        im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")
        self.title.draw(im, t)
        self.caps.draw(im, t)
        return im.convert("RGB")


def main():
    sid = sys.argv[1]
    story = next(s for s in STORIES if s["id"] == sid)
    global DUR
    DUR = story.get("dur", 30.0)
    words = prepare_voice(story)
    ev = event_times(sid, words)
    print(sid, "voice", round(words[-1][1], 2), "events", ev, flush=True)
    r = Renderer(story, words, ev)
    if "--preview" in sys.argv:
        os.makedirs(os.path.join(BUILD, "preview"), exist_ok=True)
        times = [float(x) for x in sys.argv[sys.argv.index("--preview") + 1:]] or [2, 10, ev["climax"] + 1.5, 28]
        for t in times:
            r.frame(t).save(os.path.join(BUILD, "preview", f"{sid}_{t:05.1f}.jpg"), quality=85)
        return
    wav = make_audio(story, ev)
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, sid + ".mp4")
    p = subprocess.Popen([FF, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-i", wav, "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
                          out], stdin=subprocess.PIPE)
    n = int(DUR * FPS)
    for i in range(n):
        p.stdin.write(r.frame(i / FPS).tobytes())
        if i % 150 == 0:
            print(sid, f"{i}/{n}", flush=True)
    p.stdin.close()
    p.wait()
    print("done", out, flush=True)


if __name__ == "__main__":
    main()
