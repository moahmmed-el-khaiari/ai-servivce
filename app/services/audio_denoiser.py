"""
audio_denoiser.py
-----------------
Pipeline de débruitage audio pour appels téléphoniques Twilio → Whisper.

Niveaux de traitement :
  1. Upsampling 8kHz → 16kHz  (audioop)
  2. Filtres passe-haut/bas    (scipy)
  3. RNNoise                   (réseau de neurones — supprime bruit fond, souffle, écho)
  4. Normalisation RMS + soft clip

Installation :
  pip install rnnoise-wrapper --break-system-packages
  # ou : pip install torch denoiser --break-system-packages  (alternative DeepFilterNet)
"""
import io
import wave
import audioop
import subprocess
import tempfile
import os
import numpy as np
from scipy import signal as scipy_signal

# ─────────────────────────────────────────────
# Import RNNoise (avec fallback si non installé)
# ─────────────────────────────────────────────
try:
    import rnnoise
    RNNOISE_AVAILABLE = True
    print("[DENOISER] RNNoise disponible ✅")
except ImportError:
    RNNOISE_AVAILABLE = False
    print("[DENOISER] RNNoise non installé — fallback filtres scipy uniquement")
    print("[DENOISER] Installez avec : pip install rnnoise-wrapper --break-system-packages")

# Alternative : DeepFilterNet (meilleure qualité, plus lent)
try:
    from df.enhance import enhance, init_df
    DF_MODEL, DF_STATE, _ = init_df()
    DEEPFILTER_AVAILABLE = True
    print("[DENOISER] DeepFilterNet disponible ✅")
except ImportError:
    DEEPFILTER_AVAILABLE = False


# ─────────────────────────────────────────────
# NIVEAU 1 : Upsampling 8kHz → 16kHz
# ─────────────────────────────────────────────
def upsample_8k_to_16k(pcm_8k: bytes) -> bytes:
    """Convertit PCM 8kHz mono int16 → 16kHz avec antialiasing."""
    pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
    return pcm_16k


# ─────────────────────────────────────────────
# NIVEAU 2 : Filtres spectraux (scipy)
# ─────────────────────────────────────────────
def apply_telephone_filters(pcm: bytes, sample_rate: int = 16000) -> bytes:
    """
    Filtre la bande téléphonique :
    - Passe-haut 120Hz  : supprime souffle de ligne, bruit BF
    - Accentuation 1500Hz : clarifie les consonnes
    - Passe-bas 3400Hz  : coupe les artefacts HF
    """
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if len(samples) == 0:
        return pcm

    nyq = sample_rate / 2

    # Passe-haut 120Hz
    b, a = scipy_signal.butter(4, 120 / nyq, btype='high')
    samples = scipy_signal.lfilter(b, a, samples)

    # Accentuation consonnes (+3dB @ 1500Hz, Q=1.5)
    b_mid, a_mid = scipy_signal.iirpeak(1500 / nyq, Q=1.5)
    samples = samples + 0.3 * scipy_signal.lfilter(b_mid, a_mid, samples)

    # Passe-bas 3400Hz
    b2, a2 = scipy_signal.butter(4, 3400 / nyq, btype='low')
    samples = scipy_signal.lfilter(b2, a2, samples)

    return samples.astype(np.int16).tobytes()


# ─────────────────────────────────────────────
# NIVEAU 3a : RNNoise (réseau de neurones léger)
# ─────────────────────────────────────────────
def apply_rnnoise(pcm: bytes, sample_rate: int = 16000) -> bytes:
    """
    RNNoise : réseau de neurones GRU entraîné sur des milliers d'heures de bruit.
    Supprime : bruit de fond, souffle, bruit de rue, musique de fond légère.
    Très rapide (< 1ms/frame), fonctionne en temps réel.

    RNNoise travaille en interne à 48kHz, la lib gère la conversion.
    """
    if not RNNOISE_AVAILABLE:
        return pcm

    try:
        denoiser = rnnoise.RNNoise()
        samples = np.frombuffer(pcm, dtype=np.int16)

        # RNNoise traite par frames de 480 samples
        frame_size = 480
        output = np.zeros_like(samples)

        for i in range(0, len(samples) - frame_size, frame_size):
            frame = samples[i:i + frame_size].astype(np.float32)
            denoised_frame = denoiser.process_frame(frame)
            output[i:i + frame_size] = denoised_frame.astype(np.int16)

        print(f"[DENOISER] RNNoise appliqué ({len(samples)//frame_size} frames)")
        return output.tobytes()

    except Exception as e:
        print(f"[DENOISER] RNNoise erreur : {e} — skip")
        return pcm


# ─────────────────────────────────────────────
# NIVEAU 3b : DeepFilterNet (alternative, meilleure qualité)
# ─────────────────────────────────────────────
def apply_deepfilter(pcm: bytes, sample_rate: int = 16000) -> bytes:
    """
    DeepFilterNet : suppression de bruit par apprentissage profond.
    Qualité supérieure à RNNoise, latence ~10ms.
    Supprime aussi l'écho et les bruits impulsionnels (claquements, klaxons).
    """
    if not DEEPFILTER_AVAILABLE:
        return pcm

    try:
        import torch
        import torchaudio

        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        tensor = torch.from_numpy(samples).unsqueeze(0)  # (1, N)

        # Rééchantillonnage vers 48kHz si nécessaire
        if sample_rate != 48000:
            tensor = torchaudio.functional.resample(tensor, sample_rate, 48000)

        enhanced = enhance(DF_MODEL, DF_STATE, tensor)

        # Rééchantillonnage retour vers sample_rate
        if sample_rate != 48000:
            enhanced = torchaudio.functional.resample(enhanced, 48000, sample_rate)

        result = (enhanced.squeeze(0).numpy() * 32767).astype(np.int16)
        print(f"[DENOISER] DeepFilterNet appliqué")
        return result.tobytes()

    except Exception as e:
        print(f"[DENOISER] DeepFilterNet erreur : {e} — skip")
        return pcm


# ─────────────────────────────────────────────
# NIVEAU 4 : Normalisation + protection saturation
# ─────────────────────────────────────────────
def normalize_audio(pcm: bytes) -> bytes:
    """
    Normalisation hybride (RMS + pic) :
    - Cible RMS ~4000 pour une voix confortable pour Whisper
    - Plafond 8x gain pour éviter d'amplifier le silence résiduel
    - Soft clipping pour éviter la distorsion numérique
    """
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if len(samples) == 0:
        return pcm

    rms  = np.sqrt(np.mean(samples**2))
    peak = np.max(np.abs(samples))

    if rms > 0 and rms < 3000:
        gain = min(4000 / rms, 12000 / max(peak, 1), 8.0)
        samples = samples * gain
        print(f"[DENOISER] Gain x{gain:.1f} (RMS {rms:.0f} → {rms*gain:.0f})")

    # Soft clipping au-dessus de 28000
    mask = np.abs(samples) > 28000
    samples[mask] = np.sign(samples[mask]) * (
        28000 + (np.abs(samples[mask]) - 28000) * 0.3
    )
    samples = np.clip(samples, -32767, 32767)

    return samples.astype(np.int16).tobytes()


# ─────────────────────────────────────────────
# PIPELINE COMPLET
# ─────────────────────────────────────────────
def denoise_wav(wav_bytes: bytes, use_deepfilter: bool = False) -> bytes:
    """
    Pipeline complet de débruitage d'un fichier WAV téléphonique.

    Args:
        wav_bytes      : WAV brut (8kHz ou 16kHz, mono, int16)
        use_deepfilter : True = DeepFilterNet (meilleure qualité, plus lent)
                         False = RNNoise (rapide, suffisant pour commandes vocales)

    Returns:
        WAV 16kHz mono int16 débruité, prêt pour Whisper
    """
    # Lire le WAV
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, 'rb') as wf:
        sample_rate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
        n_channels = wf.getnchannels()

    print(f"[DENOISER] Entrée : {sample_rate}Hz, {n_channels}ch, {len(pcm)//2} samples")

    # Mono si nécessaire
    if n_channels == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
        print("[DENOISER] Stéréo → mono")

    # Niveau 1 : Upsampling
    if sample_rate == 8000:
        pcm = upsample_8k_to_16k(pcm)
        sample_rate = 16000
        print("[DENOISER] 8kHz → 16kHz ✅")

    # Niveau 2 : Filtres spectraux
    pcm = apply_telephone_filters(pcm, sample_rate)
    print("[DENOISER] Filtres spectraux appliqués ✅")

    # Niveau 3 : Débruitage par réseau de neurones
    if use_deepfilter and DEEPFILTER_AVAILABLE:
        pcm = apply_deepfilter(pcm, sample_rate)
    elif RNNOISE_AVAILABLE:
        pcm = apply_rnnoise(pcm, sample_rate)
    else:
        print("[DENOISER] Aucun débruiteur neuronal disponible — filtres scipy seuls")

    # Niveau 4 : Normalisation
    pcm = normalize_audio(pcm)
    print("[DENOISER] Normalisation ✅")

    # Réécriture WAV
    out = io.BytesIO()
    with wave.open(out, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)

    result = out.getvalue()
    print(f"[DENOISER] Sortie : {len(result)} bytes WAV 16kHz prêt pour Whisper ✅")
    return result
