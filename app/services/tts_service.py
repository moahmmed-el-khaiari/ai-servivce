import requests
from app.config import ELEVENLABS_API_KEY

ELEVENLABS_VOICE_ID = "XB0fDUnXU5powFXDhCwa"  # Charlotte FR

def text_to_speech(text: str) -> bytes:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }

    data = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",   # ✅ modèle le plus rapide
        "language_code": "fr",
        "voice_settings": {
            "stability": 0.35,             # ✅ moins stable = plus naturel/humain
            "similarity_boost": 0.75,
            "style": 0.25,                 # ✅ un peu d'expressivité
            "use_speaker_boost": True,
            "speed": 1.15                  # ✅ légèrement plus rapide qu'humain normal
        }
    }

    try:
        print(f"[TTS] '{text[:60]}...' " if len(text) > 60 else f"[TTS] '{text}'")
        response = requests.post(url, json=data, headers=headers, timeout=15)

        if response.status_code == 401:
            print(f"[TTS] ⚠️ Compte ElevenLabs bloqué → fallback alice")
            return b""
        if response.status_code != 200:
            print(f"[TTS] Erreur {response.status_code} → fallback alice")
            return b""

        print(f"[TTS] ✅ {len(response.content)} bytes")
        return response.content

    except Exception as e:
        print(f"[TTS] Exception : {e} → fallback alice")
        return b""