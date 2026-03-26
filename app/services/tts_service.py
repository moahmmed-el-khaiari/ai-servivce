import requests
import io
from app.config import ELEVENLABS_API_KEY

ELEVENLABS_VOICE_ID = "YxrwjAKoUKULGd0g8K9Y"  # Lucie - Support Agent [fr]

# ✅ Mettre à True pour désactiver ElevenLabs et utiliser gTTS directement
# ✅ Mettre à False pour réessayer ElevenLabs (après rechargement de crédits)
_quota_exceeded = False  # ✅ False = ElevenLabs actif


# Voix edge-tts disponibles (Microsoft Azure — gratuites)
EDGE_VOICE = "fr-FR-DeniseNeural"  # Professionnelle, chaleureuse — parfaite vendeuse
# Alternatives : "fr-FR-EloiseNeural" (jeune/dynamique), "fr-FR-VivienneMultilingualNeural"


def _tts_edge(text: str) -> bytes:
    """Microsoft Edge TTS — gratuit, qualité proche ElevenLabs, voix très naturelle."""
    try:
        import edge_tts
        import asyncio

        async def _generate():
            communicate = edge_tts.Communicate(text, EDGE_VOICE, rate="+10%")
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()

        audio = asyncio.run(_generate())
        if audio:
            print(f"[TTS] ✅ edge-tts ({EDGE_VOICE}) — {len(audio)} bytes")
            return audio
    except ImportError:
        print("[TTS] edge-tts non installé — pip install edge-tts")
    except Exception as e:
        print(f"[TTS] edge-tts exception: {e}")
    return b""


def _tts_gtts(text: str) -> bytes:
    """Google TTS — fallback si edge-tts indisponible."""
    try:
        from gtts import gTTS
        buf = io.BytesIO()
        tts = gTTS(text=text, lang="fr", tld="fr", slow=False)
        tts.write_to_fp(buf)
        audio = buf.getvalue()
        print(f"[TTS] ✅ gTTS — {len(audio)} bytes")
        return audio
    except ImportError:
        print("[TTS] gTTS non installé — pip install gtts")
    except Exception as e:
        print(f"[TTS] gTTS exception: {e}")
    return b""


def text_to_speech(text: str) -> bytes:
    global _quota_exceeded

    print(f"[TTS] '{text[:60]}...'" if len(text) > 60 else f"[TTS] '{text}'")

    # Niveau 1 : ElevenLabs (si crédits disponibles)
    if not _quota_exceeded:
        audio = _tts_elevenlabs(text)
        if audio:
            return audio
    else:
        print("[TTS] ElevenLabs désactivé → gTTS")

    # Niveau 2 : edge-tts Microsoft (gratuit, très naturel)
    audio = _tts_edge(text)
    if audio:
        return audio

    # Niveau 3 : gTTS Google (fallback)
    audio = _tts_gtts(text)
    if audio:
        return audio

    # Niveau 3 : Polly.Lea via Twilio <Say>
    print("[TTS] Fallback Polly.Lea (Twilio)")
    return b""


def _tts_elevenlabs(text: str) -> bytes:
    global _quota_exceeded

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    data = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",   # ✅ compatible plan Free
        "language_code": "fr",
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.75,
            "style": 0.20,
            "use_speaker_boost": True,
            "speed": 1.10
        }
    }

    try:
        response = requests.post(url, json=data, headers=headers, timeout=15)

        if response.status_code == 401:
            _quota_exceeded = True
            try:
                detail = response.json().get("detail", {})
                if isinstance(detail, dict) and detail.get("status") == "quota_exceeded":
                    print("[TTS] ⚠️ Quota ElevenLabs épuisé → gTTS permanent")
                else:
                    print("[TTS] ❌ 401 clé invalide → gTTS permanent")
            except:
                print("[TTS] ❌ 401 → gTTS permanent")
            return b""

        if response.status_code == 402:
            _quota_exceeded = True
            print("[TTS] ❌ 402 crédits insuffisants → gTTS permanent")
            return b""

        if response.status_code == 404:
            _quota_exceeded = True
            print(f"[TTS] ❌ 404 voice ID introuvable → gTTS permanent")
            return b""

        if response.status_code != 200:
            print(f"[TTS] ❌ Erreur {response.status_code} : {response.text[:200]} → gTTS")
            return b""

        print(f"[TTS] ✅ ElevenLabs — {len(response.content)} bytes")
        return response.content

    except Exception as e:
        print(f"[TTS] Exception : {e}")
        return b""


def reset_quota_flag():
    """Appeler après rechargement de crédits ElevenLabs sans redémarrer."""
    global _quota_exceeded
    _quota_exceeded = False
    print("[TTS] ✅ ElevenLabs réactivé")


def list_available_voices() -> None:
    """Affiche toutes les voix disponibles sur votre compte."""
    response = requests.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": ELEVENLABS_API_KEY}
    )
    if response.status_code != 200:
        print(f"[TTS] Erreur {response.status_code} : {response.text}")
        return

    voices = response.json().get("voices", [])
    print(f"\n{'─'*50}")
    print(f"  {len(voices)} voix disponibles :")
    print(f"{'─'*50}")
    for v in voices:
        lang = v.get("labels", {}).get("language", "?")
        print(f"  {v['name']:<25} {v['voice_id']}  [{lang}]")
    print(f"{'─'*50}\n")


if __name__ == "__main__":
    list_available_voices()