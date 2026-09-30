import ssl, asyncio
import edge_tts.communicate as c
c._SSL_CTX = ssl.create_default_context(cafile="/root/.ccr/ca-bundle.crt")
import edge_tts

async def _run(text, voice, out, rate, pitch):
    com = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    subs = []
    with open(out, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                subs.append((ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]))
    return subs

def synth(text, voice, out, rate="-6%", pitch="-2Hz", tries=5):
    """Озвучка через нейросетевой голос; возвращает тайминги слов [(start, end, word)]."""
    for i in range(tries):
        try:
            return asyncio.run(_run(text, voice, out, rate, pitch))
        except Exception as e:
            print("tts retry", i, e)
    raise RuntimeError("TTS failed")
