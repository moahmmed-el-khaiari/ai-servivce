import requests
import time
from app.config import ASSEMBLYAI_API_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN

UPLOAD_URL     = "https://api.assemblyai.com/v2/upload"
TRANSCRIPT_URL = "https://api.assemblyai.com/v2/transcript"

def speech_to_text(audio_url: str) -> str:

    headers_assembly = {
        "authorization": ASSEMBLYAI_API_KEY,
        "content-type": "application/json"
    }

    # ÉTAPE 1 : Télécharger l'audio depuis Twilio avec auth
    print(f"[STT] Téléchargement audio Twilio...")
    audio_response = requests.get(
        audio_url,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        timeout=15
    )

    if audio_response.status_code != 200:
        print(f"[STT] Erreur téléchargement : {audio_response.status_code}")
        return ""

    print(f"[STT] Audio téléchargé : {len(audio_response.content)} bytes")

    # ÉTAPE 2 : Upload vers AssemblyAI
    print("[STT] Upload vers AssemblyAI...")
    upload_response = requests.post(
        UPLOAD_URL,
        headers={"authorization": ASSEMBLYAI_API_KEY},
        data=audio_response.content,
        timeout=30
    )

    if upload_response.status_code != 200:
        print(f"[STT] Erreur upload : {upload_response.status_code}")
        return ""

    upload_url = upload_response.json().get("upload_url")
    print(f"[STT] Upload OK")

    # ÉTAPE 3 : Créer la transcription
    transcript_response = requests.post(
        TRANSCRIPT_URL,
        headers=headers_assembly,
        json={
            "audio_url": upload_url,
            "language_code": "fr",
            "speech_models": ["universal-2"]   # ✅ valeur correcte
        },
        timeout=15
    )

    if transcript_response.status_code != 200:
        print(f"[STT] Erreur transcript : {transcript_response.status_code} — {transcript_response.text}")
        return ""

    transcript_id = transcript_response.json().get("id")
    print(f"[STT] Transcript ID : {transcript_id}")

    # ÉTAPE 4 : Polling
    for attempt in range(20):
        time.sleep(3)

        poll = requests.get(
            f"{TRANSCRIPT_URL}/{transcript_id}",
            headers=headers_assembly,
            timeout=10
        ).json()

        status = poll.get("status")
        print(f"[STT] Status ({attempt+1}/20): {status}")

        if status == "completed":
            text = poll.get("text", "")
            print(f"[STT] ✅ Résultat : '{text}'")
            return text

        if status == "error":
            print(f"[STT] ❌ Erreur : {poll.get('error')}")
            return ""

    print("[STT] Timeout")
    return ""