import requests
from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, GROQ_API_KEY

# ✅ Phrases parasites connues
NOISE_PHRASES = [
    "amara", "sous-titres", "communauté", "transcription automatique",
    "subtitles", "caption", "translate", "youtube", "creative commons",
    "droits réservés", "all rights reserved", "music", "musique"
]

VALID_SHORT = ["s", "m", "l", "xl", "oui", "non", "ok", "nan", "ouais"]

def is_noise(text: str) -> bool:
    cleaned = text.strip().lower().rstrip('.')
    if cleaned in VALID_SHORT:
        return False
    if len(cleaned) < 3:
        return True
    if any(phrase in cleaned for phrase in NOISE_PHRASES):
        return True
    return False


def speech_to_text(audio_url: str) -> str:

    # ÉTAPE 1 : Télécharger audio depuis Twilio
    print("[STT] Téléchargement audio Twilio...")
    audio_response = requests.get(
        audio_url,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        timeout=15
    )

    if audio_response.status_code != 200:
        print(f"[STT] Erreur téléchargement : {audio_response.status_code}")
        return ""

    audio_size = len(audio_response.content)
    print(f"[STT] Audio téléchargé : {audio_size} bytes")

    if audio_size < 3000:
        print(f"[STT] Audio trop court ({audio_size} bytes) — ignoré")
        return ""

    # ÉTAPE 2 : Envoyer à Groq Whisper
    print("[STT] Envoi à Groq Whisper...")
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}"
            },
            files={
                "file": ("audio.mp3", audio_response.content, "audio/mpeg")
            },
            data={
                "model": "whisper-large-v3",
                "language": "fr",
                # ✅ Prompt guide Whisper vers vocabulaire restaurant
                "prompt": (
                    "commande restaurant savoria. "
                    "pizza cafe boisson dessert menu. "
                    "petit moyen grand tres grand. "
                    "oui non confirmer annuler."
                ),
                "response_format": "json",
                "temperature": 0
            },
            timeout=30
        )
    except Exception as e:
        print(f"[STT] Erreur réseau Groq : {e}")
        return ""

    if response.status_code != 200:
        print(f"[STT] Erreur Groq : {response.status_code} — {response.text}")
        return ""

    text = response.json().get("text", "").strip()

    # Filtre bruit
    if is_noise(text):
        print(f"[STT] Bruit détecté — ignoré : '{text}'")
        return ""

    print(f"[STT] ✅ Résultat : '{text}'")
    return text