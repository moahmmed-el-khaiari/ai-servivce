import uuid
import re
import json
import base64
import asyncio
import audioop
import io
import wave
import time
import numpy as np
from pathlib import Path

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, FileResponse
from twilio.twiml.voice_response import VoiceResponse, Connect
from twilio.rest import Client as TwilioClient

from app.config import NGROK_BASE_URL, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
from app.services.tts_service import text_to_speech
from app.state_machine.conversation_manager import clear_session, get_session
from app.state_machine.conversation_states import ConversationState
from app.services.voice_order_service import handle_voice_order
from app.services.stt_service import groq_transcribe_pcm

router = APIRouter()

AUDIO_DIR = Path("audio_files")
AUDIO_DIR.mkdir(exist_ok=True)

# =============================
# VAD CONFIG
# =============================
SILENCE_THRESHOLD    = 200
SILENCE_DURATION_MS  = 1800   # pour commandes longues
SILENCE_SHORT_MS     = 800    # ✅ pour oui/non/taille
MIN_SPEECH_MS        = 200    # ✅ accepte mots très courts
CHUNK_MS             = 20
SAMPLE_RATE          = 8000

# États qui attendent une réponse courte
SHORT_REPLY_STATES = [
    ConversationState.DRINK_OFFER,
    ConversationState.DESSERT_OFFER,
    ConversationState.CONFIRMATION,
    ConversationState.ASK_SIZE,
]

def get_silence_needed(caller_phone: str) -> int:
    """Retourne le nombre de chunks silence selon l'état actuel"""
    try:
        session = get_session(caller_phone)
        state   = session.get("state")
        if state in SHORT_REPLY_STATES:
            return int(SILENCE_SHORT_MS / CHUNK_MS)   # 40 chunks
        else:
            return int(SILENCE_DURATION_MS / CHUNK_MS) # 90 chunks
    except:
        return int(SILENCE_DURATION_MS / CHUNK_MS)

def clean_for_tts(text: str) -> str:
    return re.sub(r'[^\w\s\.,!?;:\-àâäéèêëîïôùûüçÀÂÄÉÈÊËÎÏÔÙÛÜÇ]', '', text).strip()

def compute_rms(pcm_bytes: bytes) -> float:
    if len(pcm_bytes) < 2:
        return 0.0
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples ** 2)))

def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


# =============================
# NETTOYAGE AUDIO
# =============================
def _cleanup_audio_files():
    """Supprime les fichiers MP3 de plus de 5 minutes"""
    import time
    now     = time.time()
    deleted = 0
    for f in AUDIO_DIR.glob("*.mp3"):
        try:
            age = now - f.stat().st_mtime
            if age > 300:  # 5 minutes
                f.unlink()
                deleted += 1
        except Exception:
            pass
    if deleted:
        print(f"[Cleanup] 🗑️  {deleted} fichier(s) audio supprimé(s)")


# =============================
# ROUTE /voice-entry
# =============================
@router.post("/voice-entry")
async def voice_entry(request: Request):
    form = await request.form()
    caller_phone = form.get("From", "unknown")
    call_sid     = form.get("CallSid", "unknown")

    print(f"\n{'='*50}")
    print(f"[Twilio] CallSid : {call_sid}")
    print(f"[Twilio] From    : {caller_phone}")
    print(f"{'='*50}\n")

    clear_session(caller_phone)

    resp = VoiceResponse()

    accueil_text = "Savoria, bonjour ! Je prends votre commande."
    audio_bytes = await asyncio.get_event_loop().run_in_executor(
        None, lambda: text_to_speech(accueil_text)
    )
    if audio_bytes:
        filename = f"{uuid.uuid4()}.mp3"
        (AUDIO_DIR / filename).write_bytes(audio_bytes)
        resp.play(f"{NGROK_BASE_URL}/audio/{filename}")
    else:
        resp.say(accueil_text, voice="alice", language="fr-FR")

    connect = Connect()
    s = connect.stream(url=f"wss://{NGROK_BASE_URL.replace('https://', '')}/media-stream")
    s.parameter(name="caller",   value=caller_phone)
    s.parameter(name="call_sid", value=call_sid)
    resp.append(connect)

    return Response(str(resp), media_type="application/xml")


# =============================
# WEBSOCKET /media-stream
# =============================
@router.websocket("/media-stream")
async def media_stream(ws: WebSocket):
    await ws.accept()

    caller_phone = "unknown"
    call_sid     = "unknown"

    print(f"[MediaStream] ✅ WebSocket accepté")

    audio_buffer    = bytearray()
    silence_chunks  = 0
    speech_chunks   = 0
    is_speaking_vad = False
    is_processing   = False
    stream_sid      = None
    speech_needed   = int(MIN_SPEECH_MS / CHUNK_MS)  # 10 chunks

    async def send_audio_to_call(text: str):
        nonlocal is_processing

        t0 = time.time()
        audio_bytes = await asyncio.get_event_loop().run_in_executor(
            None, lambda: text_to_speech(text)
        )
        print(f"[PIPELINE] TTS  : {time.time()-t0:.2f}s — {len(audio_bytes) if audio_bytes else 0} bytes")

        reply_clean = clean_for_tts(text)
        # ✅ Normaliser pour comparaison — enlever accents
        import unicodedata
        reply_normalized = ''.join(
            c for c in unicodedata.normalize('NFD', reply_clean.lower())
            if unicodedata.category(c) != 'Mn'
        )
        resp = VoiceResponse()
        END_PHRASES = [
            "commande confirm",   # "confirmée", "confirmee"
            "commande annul",     # "annulée", "annulee"
            "bonne journ",        # "journée", "journee"
            "veuillez rappeler",
            "lien par sms",       # fin après envoi lien
            "bonne journ",
        ]

        if audio_bytes:
            filename = f"{uuid.uuid4()}.mp3"
            (AUDIO_DIR / filename).write_bytes(audio_bytes)
            resp.play(f"{NGROK_BASE_URL}/audio/{filename}")
        else:
            resp.say(reply_clean, voice="alice", language="fr-FR")

        if any(p in reply_normalized for p in END_PHRASES):
            resp.hangup()
            print("[Voice] 📞 Hangup")
        else:
            connect = Connect()
            s = connect.stream(url=f"wss://{NGROK_BASE_URL.replace('https://', '')}/media-stream")
            s.parameter(name="caller",   value=caller_phone)
            s.parameter(name="call_sid", value=call_sid)
            resp.append(connect)

        t0 = time.time()
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                        .calls(call_sid).update(twiml=str(resp))
            )
            print(f"[PIPELINE] CALL : {time.time()-t0:.2f}s — appel mis à jour ✅")
        except Exception as e:
            if "21220" in str(e):
                print("[Voice] ⚠️ Appel terminé — ignoré")
            else:
                print(f"[Voice] ❌ {e}")

        is_processing = False

    async def process_speech(pcm_data: bytes):
        nonlocal is_processing
        t_total = time.time()
        print(f"\n{'─'*40}")
        print(f"[PIPELINE] Début — {len(pcm_data)} bytes PCM")

        try:
            t0 = time.time()
            wav_bytes  = pcm_to_wav(pcm_data, SAMPLE_RATE)
            transcript = await groq_transcribe_pcm(wav_bytes)
            print(f"[PIPELINE] STT  : {time.time()-t0:.2f}s — '{transcript}'")

            if not transcript or len(transcript.strip()) < 2:
                print("[STT] Vide — ignoré")
                is_processing = False
                return

            t0 = time.time()
            reply_text = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: handle_voice_order(
                    session_id=caller_phone,
                    message=transcript,
                    phone_override=caller_phone
                )
            )
            print(f"[PIPELINE] LLM  : {time.time()-t0:.2f}s — '{reply_text[:60]}'")

            await send_audio_to_call(reply_text)

            print(f"[PIPELINE] TOTAL: {time.time()-t_total:.2f}s")
            print(f"{'─'*40}\n")

        except Exception as e:
            print(f"[Process] ❌ {e}")
            await send_audio_to_call("Desole, une erreur est survenue. Veuillez repeter.")
            is_processing = False

    try:
        while True:
            message = await ws.receive_text()
            data  = json.loads(message)
            event = data.get("event")

            if event == "start":
                stream_sid   = data["start"]["streamSid"]
                call_sid     = data["start"].get("callSid", "unknown")
                custom       = data["start"].get("customParameters", {})
                caller_phone = custom.get("caller", "unknown")
                print(f"[MediaStream] Stream  : {stream_sid}")
                print(f"[MediaStream] CallSid : {call_sid} / Caller : {caller_phone}")

            elif event == "media":
                if is_processing:
                    continue

                payload = data["media"]["payload"]
                mulaw   = base64.b64decode(payload)
                pcm     = audioop.ulaw2lin(mulaw, 2)

                rms = compute_rms(pcm)
                audio_buffer.extend(pcm)

                if rms > SILENCE_THRESHOLD:
                    silence_chunks = 0
                    speech_chunks += 1
                    if not is_speaking_vad:
                        is_speaking_vad = True
                        print(f"[VAD] 🎤 Parole (rms={rms:.0f})")
                else:
                    if is_speaking_vad:
                        silence_chunks += 1
                        # ✅ Silence dynamique selon état conversation
                        silence_needed = get_silence_needed(caller_phone)
                        if silence_chunks >= silence_needed:
                            if speech_chunks >= speech_needed:
                                is_processing   = True
                                is_speaking_vad = False
                                speech_audio    = bytes(audio_buffer)
                                audio_buffer    = bytearray()
                                silence_chunks  = 0
                                speech_chunks   = 0
                                asyncio.create_task(process_speech(speech_audio))
                            else:
                                audio_buffer    = bytearray()
                                silence_chunks  = 0
                                speech_chunks   = 0
                                is_speaking_vad = False

            elif event == "stop":
                print("[MediaStream] Stream arrêté")
                # ✅ Nettoyer les fichiers audio de cet appel
                _cleanup_audio_files()
                break

    except WebSocketDisconnect:
        print("[MediaStream] WebSocket déconnecté")
    except Exception as e:
        print(f"[MediaStream] ❌ {e}")


# =============================
# SERVE AUDIO
# =============================
@router.get("/audio/{filename}")
async def serve_audio(filename: str):
    if "/" in filename or ".." in filename:
        return Response(content="Invalid", status_code=400)
    filepath = AUDIO_DIR / filename
    if not filepath.exists():
        return Response(content="Not found", status_code=404)
    return FileResponse(path=str(filepath), media_type="audio/mpeg")