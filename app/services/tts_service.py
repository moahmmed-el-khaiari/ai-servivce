import requests
from app.config import ELEVENLABS_API_KEY

ELEVENLABS_VOICE_ID = "XB0fDUnXU5powFXDhCwa"

def text_to_speech(text: str) -> bytes:
    """
    TTS via ElevenLabs.
    Si le compte ElevenLabs est bloqué → retourne b"" 
    → twilio_voice.py utilisera automatiquement la voix alice comme fallback
    """

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }

    data = {
    "text": text,
    "model_id": "eleven_turbo_v2_5",
    "language_code": "fr",          # ✅ force le français
    "voice_settings": {
        "stability": 0.5,
        "similarity_boost": 0.75
    }
}

    try:
        print(f"[TTS] Génération audio...")
        response = requests.post(url, json=data, headers=headers, timeout=30)

        if response.status_code == 401:
            print(f"[TTS] ⚠️ Compte ElevenLabs bloqué → fallback voix Twilio alice")
            return b""

        if response.status_code != 200:
            print(f"[TTS] Erreur {response.status_code} → fallback voix Twilio alice")
            return b""

        print(f"[TTS] ✅ Audio généré : {len(response.content)} bytes")
        return response.content

    except Exception as e:
        print(f"[TTS] Exception : {e} → fallback voix Twilio alice")
        return b""