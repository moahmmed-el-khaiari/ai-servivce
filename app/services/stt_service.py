import io
import wave
import audioop
import requests
import aiohttp
import re
import numpy as np
from scipy import signal as scipy_signal
from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, GROQ_API_KEY

# =============================
# PHRASES PARASITES
# =============================
NOISE_PHRASES = [
    "amara", "sous-titr",
    "communauté", "transcription automatique",
    "subtitles", "caption", "translate", "youtube", "creative commons",
    "droits réservés", "all rights reserved", "music", "musique",
    "merci de votre attention",
    "abonnez-vous", "like et abonnez", "n'oubliez pas de",
    "oui non confirmer annuler",
    "petit moyen grand tres grand",
    "pizza cafe boisson dessert",
    "commande restaurant savoria",
    "restaurant savoria",
    # Hallucinations vues dans les logs
    "société radio", "hablau", "c'est parti", "would",
    "radio canada", "radio-canada",
    # ✅ Nouvelles hallucinations vues dans les logs
    "fondateur à son", "fondateur",
    "puls magie", "beter man", "coc lambat", "lambat décids",
    "weinvest", "this video", "junk anyway",
    "however", "anyway", "per au moins",
    # ✅ Mots anglais isolés = hallucinations
    # ⚠️ "firme" RETIRÉ — substring de "confirme" (mot valide français)
    "drowned", "cracking", "sentry", "spring george", "jump",
    "empathie", "podih", "temoign", "brow",
    "sucrer eye", "merite a sucrer", "pe duy",
]

VALID_SHORT = [
    "s", "m", "l", "xl",
    "oui", "non", "ok", "nan", "ouais", "si",
    "un", "une", "deux", "trois",
    "petit", "petite", "moyen", "moyenne", "grand", "grande",
    "merci",
]

FRENCH_COMMON_WORDS = {
    "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles",
    "le", "la", "les", "un", "une", "des", "du", "de", "au", "aux",
    "et", "ou", "mais", "donc", "car", "ni", "que", "qui",
    "est", "suis", "es", "sont", "a", "ai", "as", "ont", "avons", "avez",
    "oui", "non", "pas", "ne", "plus", "bien", "bon", "tout",
    "avec", "pour", "dans", "sur", "par", "en", "ce", "cette",
    "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses",
    "ça", "cela", "voila", "merci", "bonjour", "bonsoir",
    "veux", "voudrais", "prends", "donne", "donnez", "moi",
    "petit", "petite", "moyen", "moyenne", "grand", "grande",
    # ✅ Produits du menu — éviter filtrage hallucination
    "pizza", "cafe", "coca", "eau", "menu", "taille", "commande",
    "tiramisu", "burger", "cheesecake", "margherita",
    # ✅ Déformations STT connues — laisser passer pour product_matcher
    "keramisu", "kiramisu", "tiramichou",
    "tacos", "taquette", "tacot", "poulet", "pate",
    "s'il", "plait", "aussi", "encore", "autre", "même",
    "voudrais", "commander", "prendre", "avoir",
    "veux", "vient", "jour", "fois", "chose", "rien", "tout",
    "prend", "prendre", "peux", "peut", "faut", "veut",
    "alors", "donc", "votre", "notre", "leur", "leurs",
    # ✅ Mots de confirmation — protégés contre le filtrage
    "confirme", "confirmez", "confirmation", "confirmer",
}


def is_noise(text: str) -> bool:
    cleaned = text.strip().lower().rstrip('.')

    if cleaned in VALID_SHORT:
        return False
    if len(cleaned) < 3:
        return True

    # ✅ Phrases de confirmation — jamais filtrées
    ALWAYS_VALID = {
        "oui je confirme", "je confirme", "oui confirme",
        "ah oui je confirme", "oui bien sur je confirme",
        "savoria ah oui je confirme",
    }
    if any(v in cleaned for v in ALWAYS_VALID):
        return False

    # Détecter tout caractère non-latin
    for char in cleaned:
        if ord(char) > 1000:
            print(f"[STT] Caractère non-latin détecté '{char}' → hallucination ignorée")
            return True

    # Détecter les caractères spéciaux nordiques
    NON_FRENCH_LATIN = set("ðþýøæőűšžčřňťďůłşğıñ")
    if any(c in NON_FRENCH_LATIN for c in cleaned):
        print(f"[STT] Caractère non-français détecté → hallucination ignorée : '{cleaned}'")
        return True

    # Vérifier les phrases parasites
    for phrase in NOISE_PHRASES:
        if phrase in cleaned:
            print(f"[STT] Phrase parasite détectée '{phrase}' → hallucination ignorée")
            return True

    # Vérifier ratio français
    words = set(re.sub(r"[^\w\s]", "", cleaned).split())
    if len(words) >= 2:
        french_count = sum(1 for w in words if w in FRENCH_COMMON_WORDS)
        french_ratio = french_count / len(words)
        if french_ratio < 0.10:  # ✅ seuil baissé — évite filtrage de phrases mixtes
            print(f"[STT] Ratio français trop bas ({french_count}/{len(words)} = {french_ratio:.0%}) → hallucination : '{cleaned}'")
            return True

    # ✅ Fix — tiramisu retiré de prompt_words pour ne pas le filtrer
    prompt_words = {"savoria", "pepperoni", "fromages"}
    words_set = set(cleaned.split())
    if len(words_set) <= 3 and words_set.issubset(prompt_words):
        return True

    # ✅ Filtre sémantique restaurant — mots hors contexte
    # Si le texte ne contient AUCUN mot lié à la commande ou au dialogue
    CONTEXT_WORDS = {
        # Commande
        "commander", "commande", "veux", "voudrais", "prendre", "demander",
        "avoir", "prend", "peux", "vais", "je", "nous", "voudrais", "aimerai",
        # Produits menu Savoria
        "pizza", "tacos", "burger", "coca", "eau", "cafe", "jus", "milkshake",
        "tiramisu", "cheesecake", "dessert", "boisson", "menu", "margherita",
        "poulet", "viande", "hachee", "fromage", "fromages", "quatre", "reine",
        "milkshakes", "limonade", "fondant", "chocolat", "sandwich", "frites",
        # Tailles
        "petit", "petite", "moyen", "moyenne", "grand", "grande", "taille",
        # Réponses directes
        "oui", "non", "ok", "confirme", "confirmez", "confirmer",
        "annule", "merci", "bonjour", "bonsoir",
        # Mots français courants de conversation
        "pour", "avec", "un", "une", "des", "le", "la", "et", "aussi",
        "plus", "bien", "aujourd", "fois", "vais", "voudrais", "comme",
        "demander", "prendre", "avoir", "sil", "plait", "stp",
    }
    words_list = set(re.sub(r"[^\w\s]", "", cleaned).split())
    if len(words_list) >= 3:
        context_count = sum(1 for w in words_list if w in CONTEXT_WORDS)
        if context_count == 0:
            print(f"[STT] Hors contexte restaurant ({context_count}/{len(words_list)}) → ignoré : '{cleaned}'")
            return True

    return False


# =============================
# PRÉTRAITEMENT AUDIO
# =============================
def enhance_audio(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    if len(samples) == 0:
        return pcm_bytes

    nyq = sample_rate / 2

    b, a = scipy_signal.butter(4, 120 / nyq, btype='high')
    samples = scipy_signal.lfilter(b, a, samples)

    b_mid, a_mid = scipy_signal.iirpeak(1500 / nyq, Q=1.5)
    samples = samples + 0.3 * scipy_signal.lfilter(b_mid, a_mid, samples)

    samples = _spectral_noise_gate(samples, sample_rate)

    b2, a2 = scipy_signal.butter(4, 3400 / nyq, btype='low')
    samples = scipy_signal.lfilter(b2, a2, samples)

    peak = np.max(np.abs(samples))
    rms  = np.sqrt(np.mean(samples**2)) if len(samples) > 0 else 0

    if peak > 0 and rms > 0:
        target_rms = 5000  # ✅ cible plus haute pour mieux entendre
        if rms < target_rms:
            gain = min(target_rms / rms, 28000 / max(peak, 1), 12.0)  # gain max augmenté
            if gain > 1.0:
                samples = samples * gain
                print(f"[STT] Gain x{gain:.1f} (RMS={rms:.0f} → {rms*gain:.0f})")
            else:
                print(f"[STT] Audio OK (RMS={rms:.0f}, peak={peak:.0f}) — pas de gain nécessaire")
        else:
            print(f"[STT] Audio fort (RMS={rms:.0f}) — pas de gain nécessaire")

    samples = _soft_clip(samples, threshold=28000)
    return samples.astype(np.int16).tobytes()


def _spectral_noise_gate(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    frame_size  = int(sample_rate * 20 / 1000)
    n_bg_frames = min(10, len(samples) // frame_size)
    if n_bg_frames == 0:
        return samples

    bg_energy = sum(
        np.mean(samples[i * frame_size:(i + 1) * frame_size]**2)
        for i in range(n_bg_frames)
    ) / n_bg_frames
    noise_floor = bg_energy * 3.0

    output = samples.copy()
    for i in range(len(samples) // frame_size):
        s, e = i * frame_size, (i + 1) * frame_size
        if np.mean(samples[s:e]**2) < noise_floor:
            output[s:e] = samples[s:e] * 0.1
    return output


def _soft_clip(samples: np.ndarray, threshold: float = 28000) -> np.ndarray:
    above = np.abs(samples) > threshold
    samples[above] = np.sign(samples[above]) * (
        threshold + (np.abs(samples[above]) - threshold) * 0.3
    )
    return np.clip(samples, -32767, 32767)


def upsample_wav_8k_to_16k(wav_bytes: bytes) -> bytes:
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, 'rb') as wf:
        sample_rate = wf.getframerate()
        pcm_data    = wf.readframes(wf.getnframes())

    if sample_rate == 8000:
        pcm_data, _ = audioop.ratecv(pcm_data, 2, 1, 8000, 16000, None)
        sample_rate  = 16000
        print(f"[STT] Upsampling 8kHz → 16kHz ✅")

    pcm_data = enhance_audio(pcm_data, sample_rate)

    out = io.BytesIO()
    with wave.open(out, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return out.getvalue()


# =============================
# SEUIL RMS MINIMUM
# =============================
RMS_MIN_THRESHOLD = 400  # ✅ remonté de 350 — évite les bruits faibles


def pcm_rms(pcm_bytes: bytes) -> float:
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples**2))) if len(samples) > 0 else 0.0


# =============================
# PROMPT WHISPER
# =============================
# ============================
# PROMPT WHISPER DYNAMIQUE
# ============================
_whisper_prompt_cache: str = ""

def _build_whisper_prompt() -> str:
    """
    Construit le prompt Whisper dynamiquement depuis Spring Boot.
    Les produits sont chargés une seule fois et mis en cache.
    Si Spring Boot est indisponible → prompt statique de secours.
    """
    global _whisper_prompt_cache

    if _whisper_prompt_cache:
        return _whisper_prompt_cache

    # Tenter de charger les produits depuis Spring Boot
    product_names = []
    try:
        import requests as _requests
        from app.config import PRODUCT_SERVICE_URL
        resp = _requests.get(PRODUCT_SERVICE_URL, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                product_names = [p["name"] for p in data if p.get("name")]
                print(f"[STT] ✅ Prompt Whisper — {len(product_names)} produits chargés")
    except Exception as e:
        print(f"[STT] ⚠️ Impossible de charger les produits pour le prompt : {e}")

    if product_names:
        menu_str = ", ".join(product_names)
    else:
        # Fallback statique si Spring Boot indisponible
        menu_str = (
            "Pizza Margherita, Pizza Poulet, Pizza Quatre Fromages, Pizza Reine, "
            "Tacos poulet, Tacos Viande Hachee, Burger Poulet, Burger Boeuf, "
            "Coca-Cola, Jus d orange, Eau, Cafe, Limonade, Milkshakes, "
            "Tiramisu, Tiramisu, Tiramisu, Cheesecake"
        )
        print("[STT] ⚠️ Prompt Whisper — fallback statique utilisé")

    # ✅ Groq limite le prompt à 896 caractères — rester en dessous
    base = (
        "Restaurant Savoria. Commande en francais. "
        "Reponses : Oui. Non. Ok. "
        "Tailles : petit, moyen, grand. "
        f"Menu : {menu_str}. "
        "Toujours en francais. Tiramisu pas keramisu. "
    )

    # Tronquer si nécessaire pour rester sous 896 chars
    MAX_CHARS = 880
    if len(base) > MAX_CHARS:
        # Raccourcir la liste des produits
        short_menu = menu_str[:MAX_CHARS - 200]
        # Couper au dernier produit complet
        last_comma = short_menu.rfind(",")
        if last_comma > 0:
            short_menu = short_menu[:last_comma]
        base = (
            "Restaurant Savoria. Commande en francais. "
            "Reponses : Oui. Non. Ok. "
            "Tailles : petit, moyen, grand. "
            f"Menu : {short_menu}. "
            "Toujours en francais. Tiramisu pas keramisu. "
        )
        print(f"[STT] Prompt tronqué : {len(base)} chars")

    print(f"[STT] ✅ Prompt Whisper — {len(product_names)} produits — {len(base)} chars")
    _whisper_prompt_cache = base
    return _whisper_prompt_cache


def clear_whisper_prompt_cache():
    """Vider le cache du prompt (utile si le menu change)."""
    global _whisper_prompt_cache
    _whisper_prompt_cache = ""
    print("[STT] Cache prompt Whisper réinitialisé")


# Propriété dynamique — appelée à chaque transcription
def get_whisper_prompt() -> str:
    return _build_whisper_prompt()


# ✅ Compatibilité avec l'ancien code qui utilise WHISPER_PROMPT directement
WHISPER_PROMPT = ""  # sera remplacé dynamiquement à chaque appel


# =============================
# STT async — Media Streams
# =============================
async def groq_transcribe_pcm(wav_bytes: bytes) -> str:
    """Transcrit WAV bytes via Groq Whisper (async)"""

    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, 'rb') as wf:
        raw_pcm = wf.readframes(wf.getnframes())
    rms = pcm_rms(raw_pcm)

    if rms < RMS_MIN_THRESHOLD:
        print(f"[STT] Audio trop faible (RMS={rms:.0f} < {RMS_MIN_THRESHOLD}) → ignoré")
        return ""

    wav_bytes = upsample_wav_8k_to_16k(wav_bytes)
    print(f"[STT] Groq async — {len(wav_bytes)} bytes WAV 16kHz (RMS brut={rms:.0f})")

    try:
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field("file", wav_bytes,
                           filename="audio.wav",
                           content_type="audio/wav")
            data.add_field("model", "whisper-large-v3-turbo")
            data.add_field("language", "fr")
            data.add_field("prompt", get_whisper_prompt())
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

    if len(audio_response.content) < 3000:
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
                "prompt": get_whisper_prompt(),
                "response_format": "json",
                "temperature": "0",
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