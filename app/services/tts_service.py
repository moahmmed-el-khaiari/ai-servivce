import requests
from app.config import ELEVENLABS_API_KEY

ELEVENLABS_VOICE_ID = "cgSgspJ2msm6clMCkdW9"  # Jessica — multilingue

# ✅ Si quota ElevenLabs épuisé, basculer automatiquement sur Twilio Alice
# (géré dans main.py — tts.py retourne b"" et main.py utilise <Say>)
_quota_exceeded = False  # flag global pour éviter les appels inutiles


def text_to_speech(text: str) -> bytes:
    global _quota_exceeded

    # Si on sait déjà que le quota est épuisé, pas la peine d'appeler l'API
    if _quota_exceeded:
        print(f"[TTS] Quota épuisé — fallback Twilio Alice")
        return b""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "language_code": "fr",
        "voice_settings": {
            "stability": 0.40,
            "similarity_boost": 0.75,
            "style": 0.20,
            "use_speaker_boost": True,
            "speed": 1.10
        }
    }

    try:
        print(f"[TTS] '{text[:60]}...'" if len(text) > 60 else f"[TTS] '{text}'")
        response = requests.post(url, json=data, headers=headers, timeout=15)

        if response.status_code == 401:
            detail = response.json().get("detail", {})
            if isinstance(detail, dict) and detail.get("status") == "quota_exceeded":
                _quota_exceeded = True
                print(f"[TTS] ⚠️ Quota ElevenLabs épuisé — fallback Twilio Alice activé")
            else:
                print(f"[TTS] ❌ 401 — clé API invalide")
            return b""

        if response.status_code == 404:
            print(f"[TTS] ❌ 404 — voice ID '{ELEVENLABS_VOICE_ID}' introuvable")
            return b""

        if response.status_code != 200:
            print(f"[TTS] ❌ Erreur {response.status_code} : {response.text[:200]}")
            return b""

        print(f"[TTS] ✅ {len(response.content)} bytes")
        return response.content

    except Exception as e:
        print(f"[TTS] Exception : {e}")
        return b""


def reset_quota_flag():
    """Appeler si vous rechargez des crédits ElevenLabs sans redémarrer le serveur."""
    global _quota_exceeded
    _quota_exceeded = False
    print("[TTS] Flag quota réinitialisé")


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