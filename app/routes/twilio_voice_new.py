import uuid
import re
from pathlib import Path

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import Response, FileResponse
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client as TwilioClient
from app.config import NGROK_BASE_URL, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
from app.services.stt_service import speech_to_text
from app.services.tts_service import text_to_speech
from app.state_machine.conversation_manager import clear_session
from app.services.voice_order_service import handle_voice_order

router = APIRouter()

AUDIO_DIR = Path("audio_files")
AUDIO_DIR.mkdir(exist_ok=True)

processed_recordings = set()

def clean_for_tts(text: str) -> str:
    return re.sub(r'[^\w\s\.,!?;:\-àâäéèêëîïôùûüçÀÂÄÉÈÊËÎÏÔÙÛÜÇ]', '', text).strip()


def process_audio(call_sid: str, caller_phone: str, recording_url: str):
    """Traitement STT + LLM + TTS en arrière-plan — répond via Twilio API"""

    # ÉTAPE 1 : STT
    transcript = speech_to_text(recording_url + ".mp3")

    if not transcript or transcript.strip() == "":
        reply_text = "Desole, je n'ai pas compris. Veuillez reessayer."
    else:
        print(f"[Voice] Transcription : '{transcript}'")
        # ÉTAPE 2 : LLM
        try:
            reply_text = handle_voice_order(
                session_id=caller_phone,
                message=transcript,
                phone_override=caller_phone
            )
        except Exception as e:
            print(f"[Voice] Erreur handle_voice_order : {e}")
            reply_text = "Une erreur est survenue. Veuillez rappeler."

    reply_clean = clean_for_tts(reply_text)
    print(f"[Voice] Réponse : '{reply_clean[:80]}'")

    # ÉTAPE 3 : TTS
    audio_bytes = text_to_speech(reply_text)

    # ÉTAPE 4 : Construire TwiML
    END_PHRASES = [
        "commande confirmee",
        "commande annulee",
        "bonne journee",
        "veuillez rappeler"
    ]

    resp = VoiceResponse()

    if audio_bytes:
        filename = f"{uuid.uuid4()}.mp3"
        filepath = AUDIO_DIR / filename
        with open(filepath, "wb") as f:
            f.write(audio_bytes)
        resp.play(f"{NGROK_BASE_URL}/audio/{filename}")
    else:
        resp.say(reply_clean, voice="alice", language="fr-FR")

    if any(phrase in reply_clean.lower() for phrase in END_PHRASES):
        resp.hangup()
        print("[Voice] 📞 Appel terminé — hangup")
    else:
        resp.record(
            max_length=15,
            action=f"{NGROK_BASE_URL}/voice-entry",
            play_beep=True,
            transcribe=False
        )

    # ÉTAPE 5 : Mettre à jour l'appel Twilio
    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.calls(call_sid).update(twiml=str(resp))
        print(f"[Voice] ✅ Appel mis à jour : {call_sid}")
    except Exception as e:
        error_msg = str(e)
        if "21220" in error_msg or "not in-progress" in error_msg:
            print(f"[Voice] ⚠️ Appel terminé avant réponse — ignoré")
        else:
            print(f"[Voice] ❌ Erreur update call : {e}")


@router.post("/voice-entry")
async def voice_entry(request: Request, background_tasks: BackgroundTasks):

    form = await request.form()
    call_sid      = form.get("CallSid", "unknown")
    caller_phone  = form.get("From", "unknown")
    recording_url = form.get("RecordingUrl")

    print(f"\n{'='*50}")
    print(f"[Twilio] CallSid  : {call_sid}")
    print(f"[Twilio] From     : {caller_phone}")
    print(f"[Twilio] Recording: {recording_url}")
    print(f"{'='*50}\n")

    # CAS 1 : Premier appel — accueil via ElevenLabs
    if not recording_url:
        print("[Voice] Premier appel → accueil")
        clear_session(caller_phone)

        accueil_text = (
            "Bonjour et bienvenue chez restaurant Savoria. "
            "Veuillez parler apres le bip pour passer votre commande."
        )

        audio_bytes = text_to_speech(accueil_text)
        resp = VoiceResponse()

        if audio_bytes:
            filename = f"{uuid.uuid4()}.mp3"
            filepath = AUDIO_DIR / filename
            with open(filepath, "wb") as f:
                f.write(audio_bytes)
            resp.play(f"{NGROK_BASE_URL}/audio/{filename}")
            print("[TTS] ✅ Accueil ElevenLabs")
        else:
            resp.say(accueil_text, voice="alice", language="fr-FR")
            print("[TTS] Fallback alice pour accueil")

        resp.record(
            max_length=15,
            action=f"{NGROK_BASE_URL}/voice-entry",
            play_beep=True,
            transcribe=False
        )
        return Response(str(resp), media_type="application/xml")

    # Éviter doublons
    if recording_url in processed_recordings:
        print("[Voice] Recording déjà traité — ignoré")
        resp = VoiceResponse()
        resp.record(
            max_length=15,
            action=f"{NGROK_BASE_URL}/voice-entry",
            play_beep=True,
            transcribe=False
        )
        return Response(str(resp), media_type="application/xml")

    processed_recordings.add(recording_url)

    # ✅ Répondre IMMÉDIATEMENT à Twilio — traitement en arrière-plan
    background_tasks.add_task(process_audio, call_sid, caller_phone, recording_url)

    resp = VoiceResponse()
    resp.pause(length=15)  # ✅ pause pour laisser le temps au background task de mettre à jour l'appel
    return Response(str(resp), media_type="application/xml")


@router.get("/audio/{filename}")
async def serve_audio(filename: str):
    if "/" in filename or ".." in filename:
        return Response(content="Invalid", status_code=400)
    filepath = AUDIO_DIR / filename
    if not filepath.exists():
        return Response(content="Not found", status_code=404)
    return FileResponse(path=str(filepath), media_type="audio/mpeg")