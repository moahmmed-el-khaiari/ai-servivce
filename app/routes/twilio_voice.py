import uuid
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response, FileResponse
from twilio.twiml.voice_response import VoiceResponse

from app.services.stt_service import speech_to_text
from app.services.tts_service import text_to_speech
from app.models.chat_models import ChatRequest

router = APIRouter()

NGROK_BASE_URL = os.getenv("NGROK_BASE_URL", "https://c6c4-161-178-132-46.ngrok-free.app")

AUDIO_DIR = Path("audio_files")
AUDIO_DIR.mkdir(exist_ok=True)

# ✅ Garde en mémoire les RecordingUrl déjà traités pour éviter les doublons
processed_recordings = set()


@router.post("/voice-entry")
async def voice_entry(request: Request):
    from app.main import chat

    form = await request.form()
    call_sid      = form.get("CallSid", "unknown")
    caller_phone  = form.get("From", "unknown")
    recording_url = form.get("RecordingUrl")

    print(f"\n{'='*50}")
    print(f"[Twilio] CallSid  : {call_sid}")
    print(f"[Twilio] From     : {caller_phone}")
    print(f"[Twilio] Recording: {recording_url}")
    print(f"{'='*50}\n")

    # CAS 1 : Premier appel — pas d'audio
    if not recording_url:
        print("[Voice] Premier appel → accueil")
        resp = VoiceResponse()
        resp.say(
            "Bonjour et bienvenue chez restaurant Savoria ! "
            "Je suis votre assistant vocal. "
            "Veuillez parler après le bip pour passer votre commande.",
            voice="alice", language="fr-FR"
        )
        resp.record(
            max_length=10,
            action=f"{NGROK_BASE_URL}/voice-entry",
            play_beep=True,
            transcribe=False
        )
        return Response(str(resp), media_type="application/xml")

    # ✅ Éviter de traiter le même enregistrement deux fois
    if recording_url in processed_recordings:
        print(f"[Voice] ⚠️ Recording déjà traité — ignoré")
        resp = VoiceResponse()
        resp.record(
            max_length=10,
            action=f"{NGROK_BASE_URL}/voice-entry",
            play_beep=True,
            transcribe=False
        )
        return Response(str(resp), media_type="application/xml")

    processed_recordings.add(recording_url)

    # ÉTAPE 1 : STT
    transcript = speech_to_text(recording_url + ".mp3")
    if not transcript or transcript.strip() == "":
        resp = VoiceResponse()
        resp.say("Désolé, je n'ai pas compris. Veuillez réessayer.", voice="alice", language="fr-FR")
        resp.record(max_length=10, action=f"{NGROK_BASE_URL}/voice-entry", play_beep=True, transcribe=False)
        return Response(str(resp), media_type="application/xml")

    print(f"[Voice] Transcription : '{transcript}'")

    # ÉTAPE 2 : LLM
    try:
        chat_response = chat(ChatRequest(session_id=caller_phone, message=transcript))
        reply_text = chat_response.reply
        print(f"[Voice] Réponse AI : '{reply_text[:80]}'")
    except Exception as e:
        print(f"[Voice] Erreur /chat : {e}")
        resp = VoiceResponse()
        resp.say("Une erreur est survenue. Veuillez rappeler.", voice="alice", language="fr-FR")
        return Response(str(resp), media_type="application/xml")

    # ÉTAPE 3 : TTS
    audio_bytes = text_to_speech(reply_text)

    if not audio_bytes:
        # Fallback voix alice
        resp = VoiceResponse()
        resp.say(reply_text, voice="alice", language="fr-FR")
        resp.record(max_length=10, action=f"{NGROK_BASE_URL}/voice-entry", play_beep=True, transcribe=False)
        return Response(str(resp), media_type="application/xml")

    # ÉTAPE 4 : Sauvegarder MP3
    filename = f"{uuid.uuid4()}.mp3"
    filepath = AUDIO_DIR / filename
    with open(filepath, "wb") as f:
        f.write(audio_bytes)

    # ÉTAPE 5 : TwiML Play + Record
    audio_url = f"{NGROK_BASE_URL}/audio/{filename}"
    resp = VoiceResponse()
    resp.play(audio_url)
    resp.record(max_length=10, action=f"{NGROK_BASE_URL}/voice-entry", play_beep=True, transcribe=False)
    return Response(str(resp), media_type="application/xml")


@router.get("/audio/{filename}")
async def serve_audio(filename: str):
    if "/" in filename or ".." in filename:
        return Response(content="Invalid", status_code=400)
    filepath = AUDIO_DIR / filename
    if not filepath.exists():
        return Response(content="Not found", status_code=404)
    return FileResponse(path=str(filepath), media_type="audio/mpeg")