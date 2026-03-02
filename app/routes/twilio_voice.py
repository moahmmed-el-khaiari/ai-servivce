from fastapi import APIRouter, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse
import requests

from app.services.stt_service import speech_to_text
from app.services.tts_service import text_to_speech
from app.main import chat
from app.models.chat_models import ChatRequest

router = APIRouter()

@router.post("/twilio-voice")
async def twilio_voice(request: Request):

    form = await request.form()

    recording_url = form.get("RecordingUrl")

    if not recording_url:
        resp = VoiceResponse()
        resp.say("Bonjour. Parlez après le bip.")
        resp.record(timeout=5, transcribe=False)
        return Response(str(resp), media_type="application/xml")

    # 1️⃣ STT
    text = speech_to_text(recording_url + ".mp3")

    # 2️⃣ LLM
    ai_response = chat(ChatRequest(
        session_id="twilio",
        message=text
    ))

    # 3️⃣ TTS
    audio_bytes = text_to_speech(ai_response.reply)

    # Save temporarily
    with open("response.mp3", "wb") as f:
        f.write(audio_bytes)

    resp = VoiceResponse()
    resp.play("https://your-domain.com/response.mp3")
    resp.record(timeout=5)

    return Response(str(resp), media_type="application/xml")