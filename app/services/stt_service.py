import io
import wave
import audioop
import requests
import aiohttp
import numpy as np
from scipy import signal as scipy_signal
from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, GROQ_API_KEY

# =============================
# PHRASES PARASITES
# =============================
NOISE_PHRASES = [
    "amara", "sous-titres", "communauté", "transcription automatique",
    "subtitles", "caption", "translate", "youtube", "creative commons",
    "droits réservés", "all rights reserved", "music", "musique",
    "oui non confirmer annuler",
    "petit moyen grand tres grand",
    "pizza cafe boisson dessert",
    "commande restaurant savoria",
    "restaurant savoria",
]

VALID_SHORT = ["s", "m", "l", "xl", "oui", "non", "ok", "nan", "ouais"]

def is_noise(text: str) -> bool:
    cleaned = text.strip().lower().rstrip('.')
    if cleaned in VALID_SHORT:
        return False
    if len(cleaned) < 3:
        return True
    for phrase in NOISE_PHRASES:
        if phrase in cleaned:
            return True
    return False


# =============================
# PRÉTRAITEMENT AUDIO
# =============================
def enhance_audio(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """
    Améliore l'audio téléphonique 8kHz avant Whisper :
    1. Filtre passe-haut — supprime le bruit de fond basse fréquence
    2. Normalisation — augmente le volume si trop faible
    3. Filtre passe-bas doux — adoucit les artefacts de compression
    """
    # Convertir en float32
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)

    if len(samples) == 0:
        return pcm_bytes

    # 1. Filtre passe-haut 80Hz — supprime rumble / bruit BF
    nyq = sample_rate / 2
    b, a = scipy_signal.butter(4, 80 / nyq, btype='high')
    samples = scipy_signal.lfilter(b, a, samples)

    # 2. Filtre passe-bas 3400Hz — bande téléphonique standard, enlève artefacts HF
    b2, a2 = scipy_signal.butter(4, 3400 / nyq, btype='low')
    samples = scipy_signal.lfilter(b2, a2, samples)

    # 3. Normalisation — amplifier si volume trop faible
    max_val = np.max(np.abs(samples))
    if max_val > 0 and max_val < 8000:
        gain = min(16000 / max_val, 6.0)  # max 6x amplification
        samples = samples * gain
        print(f"[STT] 🔊 Gain x{gain:.1f} appliqué (max={max_val:.0f})")

    # 4. Clipping protection
    samples = np.clip(samples, -32767, 32767)

    return samples.astype(np.int16).tobytes()


def upsample_wav_8k_to_16k(wav_bytes: bytes) -> bytes:
    """Rééchantillonne WAV 8kHz → 16kHz + améliore l'audio"""
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, 'rb') as wf:
        sample_rate = wf.getframerate()
        pcm_data    = wf.readframes(wf.getnframes())

    # Upsampling 8kHz → 16kHz
    if sample_rate == 8000:
        pcm_16k, _ = audioop.ratecv(pcm_data, 2, 1, 8000, 16000, None)
        out_rate   = 16000
        out_pcm    = pcm_16k
        print(f"[STT] Upsampling 8kHz → 16kHz ✅")
    else:
        out_rate = sample_rate
        out_pcm  = pcm_data

    # ✅ Amélioration audio — filtre + normalisation
    out_pcm = enhance_audio(out_pcm, out_rate)

    out = io.BytesIO()
    with wave.open(out, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(out_rate)
        wf.writeframes(out_pcm)
    return out.getvalue()


# =============================
# STT async — Media Streams
# =============================
async def groq_transcribe_pcm(wav_bytes: bytes) -> str:
    """Transcrit WAV bytes via Groq Whisper (async)"""

    wav_bytes = upsample_wav_8k_to_16k(wav_bytes)
    print(f"[STT] Groq async — {len(wav_bytes)} bytes WAV 16kHz")

    try:
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field("file", wav_bytes,
                           filename="audio.wav",
                           content_type="audio/wav")
            data.add_field("model", "whisper-large-v3-turbo")
            data.add_field("language", "fr")
            data.add_field("prompt", (
                "Commande restaurant Savoria. "
                "Tailles : petit, petite, moyen, moyenne, grand, grande, très grand. "
                "Oui, non, confirmer, annuler. "
                "Pizza margherita, pizza quatre fromages, pizza pepperoni, burger, sandwich. "
                "Tiramisu, cheesecake, fondant chocolat, tarte tatin. "
                "Coca-Cola, jus d'orange, café, eau, limonade. "
            ))
            data.add_field("temperature", "0")

            async with session.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                data=data,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"[STT] Groq erreur {resp.status}: {body}")
                    return ""
                result = await resp.json()
                text = result.get("text", "").strip()

                if is_noise(text):
                    print(f"[STT] Hallucination ignorée : '{text}'")
                    return ""

                print(f"[STT] ✅ '{text}'")
                return text

    except Exception as e:
        print(f"[STT] Exception : {e}")
        return ""


# =============================
# STT sync — recording URL
# =============================
def speech_to_text(audio_url: str) -> str:
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
        print(f"[STT] Audio trop court — ignoré")
        return ""

    return _groq_transcribe_sync(audio_response.content, "audio/mpeg")


def _groq_transcribe_sync(audio_content: bytes, content_type: str) -> str:
    print("[STT] Envoi à Groq Whisper...")
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": ("audio.mp3", audio_content, content_type)},
            data={
                "model": "whisper-large-v3-turbo",
                "language": "fr",
                "prompt": (
                    "Commande restaurant Savoria. "
                    "Tailles : petit, petite, moyen, moyenne, grand, grande, très grand. "
                    "Oui, non, confirmer, annuler. "
                    "Pizza margherita, burger, tiramisu, cheesecake, coca-cola, café. "
                ),
                "response_format": "json",
                "temperature": 0
            },
            timeout=30
        )
    except Exception as e:
        print(f"[STT] Erreur réseau : {e}")
        return ""

    if response.status_code != 200:
        print(f"[STT] Erreur Groq : {response.status_code}")
        return ""

    text = response.json().get("text", "").strip()
    if is_noise(text):
        print(f"[STT] Hallucination ignorée : '{text}'")
        return ""

    print(f"[STT] ✅ '{text}'")
    return text