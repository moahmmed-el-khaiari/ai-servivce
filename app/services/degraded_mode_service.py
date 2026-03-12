"""
degraded_mode_service.py
Gestion du mode dégradé — 4 scénarios selon le document de faisabilité
"""
from datetime import datetime
from twilio.rest import Client as TwilioClient
from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN

ONLINE_ORDER_LINK = "https://commande.restaurant.com"

# =============================
# LOG MODE DÉGRADÉ — pour le dashboard
# =============================
_degraded_log: list = []  # stocké en mémoire (à remplacer par DB si besoin)

def log_degraded_event(cause: str, caller_phone: str):
    """Enregistre un événement mode dégradé pour le dashboard restaurateur"""
    event = {
        "heure":  datetime.now().strftime("%H:%M"),
        "date":   datetime.now().strftime("%d/%m/%Y"),
        "cause":  cause,
        "statut": "redirection → commande en ligne",
        "client": caller_phone
    }
    _degraded_log.append(event)
    print(f"\n⚠️  MODE DÉGRADÉ ACTIVÉ")
    print(f"   Cause  : {cause}")
    print(f"   Client : {caller_phone}")
    print(f"   Heure  : {event['heure']}\n")

def get_degraded_log() -> list:
    """Retourne le log pour le dashboard"""
    return _degraded_log


# =============================
# ENVOI WHATSAPP LIEN
# =============================
def send_degraded_link(caller_phone: str, cause: str = ""):
    """Envoie le lien de commande en ligne par WhatsApp"""
    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=(
                f"Bonjour ! Nous rencontrons un problème technique.\n"
                f"Commandez directement en ligne en quelques secondes :\n"
                f"{ONLINE_ORDER_LINK}"
            ),
            from_="whatsapp:+14155238886",
            to=f"whatsapp:{caller_phone}"
        )
        print(f"[Mode dégradé] ✅ Lien envoyé à {caller_phone}")
    except Exception as e:
        print(f"[Mode dégradé] ❌ WhatsApp échoué : {e}")

    log_degraded_event(cause, caller_phone)


# =============================
# SCÉNARIO 4.1 — Caisse (POS) indisponible
# =============================
def handle_pos_unavailable(caller_phone: str) -> str:
    """Spring Boot / caisse ne répond pas"""
    send_degraded_link(caller_phone, cause="POS indisponible")
    return (
        "Nous rencontrons un problème technique pour enregistrer votre commande. "
        "Je vous envoie le lien pour commander en ligne. Bonne journée !"
    )


# =============================
# SCÉNARIO 4.2 — IA indisponible (LLM / STT échoue)
# =============================
def handle_ai_unavailable(caller_phone: str) -> str:
    """LLM ou STT ne répond pas"""
    send_degraded_link(caller_phone, cause="IA indisponible")
    return (
        "Je rencontre une difficulté technique pour traiter votre commande. "
        "Je vous envoie le lien pour commander directement en ligne. Bonne journée !"
    )


# =============================
# SCÉNARIO 4.3 — Paiement indisponible
# =============================
def handle_payment_unavailable(caller_phone: str) -> str:
    """Erreur lors de create_order ou lien paiement"""
    send_degraded_link(caller_phone, cause="Paiement indisponible")
    return (
        "Votre commande est prête mais le paiement par téléphone est temporairement indisponible. "
        "Je vous envoie le lien pour finaliser en ligne. Bonne journée !"
    )


# =============================
# SCÉNARIO 4.4 — Commande incompréhensible
# =============================
MAX_RETRIES = 3  # tentatives avant mode dégradé

def handle_incomprehensible_order(caller_phone: str, retry_count: int) -> str | None:
    """
    Retourne None si on peut encore réessayer.
    Retourne le message dégradé si trop de tentatives.
    """
    if retry_count < MAX_RETRIES:
        return None  # laisser voice_order_service redemander

    send_degraded_link(caller_phone, cause="Commande incompréhensible")
    return (
        "Je n'ai pas bien compris votre commande. "
        "Pour éviter toute erreur, je vous envoie le lien pour commander en ligne. Bonne journée !"
    )