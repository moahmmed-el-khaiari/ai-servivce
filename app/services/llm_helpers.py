"""
llm_helpers.py — Fonctions LLM pour interpréter les réponses vocales ambiguës
"""
import requests
from app.config import OPENROUTER_API_KEY, DEEPSEEK_MODEL, OPENROUTER_URL


def _llm_call(prompt: str, max_tokens: int = 10) -> str:
    """Appel LLM minimal"""
    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": max_tokens
            },
            timeout=8
        )
        if response.status_code != 200:
            return ""
        return response.json()["choices"][0]["message"]["content"].strip().lower()
    except Exception as e:
        print(f"[LLM Helper] Erreur : {e}")
        return ""


# =============================
# FONCTION 1 — Interpréter OUI / NON
# =============================
def interpret_yes_no(message: str) -> str | None:
    """
    Retourne "oui", "non", ou None si pas clair.
    Utilise le LLM pour comprendre les réponses ambiguës.
    """

    msg = message.strip().lower()
    msg_clean = msg.replace('.', '').replace(',', '').replace('!', '').replace('?', '').strip()

    # ✅ Détection rapide — variantes STT incluses
    FAST_YES = [
        "oui", "yes", "ok", "ouais", "bien sur", "absolument",
        "tout a fait", "affirmatif", "voila", "d accord", "daccord",
        "parfait", "super", "exact", "exactement", "c est bon",
        "wui", "voui", "mouais", "ah oui", "oh oui",
        "ui", "wi", "oue", "ouai",
    ]
    FAST_NO = [
        "non", "no", "nan", "nope", "pas du tout",
        "negatif", "jamais", "annuler",
        "bon",      # STT: "non" → "bon"
        "neu",      # STT: "non" → "neu"
        "nee", "nein", "nah",
        "bah non", "oh non", "ah non", "mais non",
        "surtout pas", "rien",
        "c est tout", "pas de",
    ]

    for w in FAST_YES:
        if w in msg_clean.split() or msg_clean == w:
            print(f"[YesNo] Fast ✅ OUI — '{message}'")
            return "oui"
    for w in FAST_NO:
        if w in msg_clean.split() or msg_clean == w:
            print(f"[YesNo] Fast ✅ NON — '{message}'")
            return "non"

    # ✅ Détection par sous-chaîne
    if any(neg in msg_clean for neg in ["non", "pas de", "rien", "c est tout", "surtout pas"]):
        print(f"[YesNo] Substring ✅ NON — '{message}'")
        return "non"
    if any(pos in msg_clean for pos in ["oui", "ouais", "d accord", "ok", "bien sur", "volontiers"]):
        print(f"[YesNo] Substring ✅ OUI — '{message}'")
        return "oui"

    # ✅ Filtre — mots sans rapport avec oui/non
    YESNO_HINTS = [
        "oui", "non", "yes", "no", "ok", "nan", "ouais", "bien", "sur",
        "accord", "absolument", "tout", "fait", "affirm", "negatif",
        "annul", "jamais", "pas", "voila", "parfait", "super",
        "bon", "neu", "nee", "nein", "nah", "wui", "voui", "rien",
        "surtout", "merci", "exact", "bah",
    ]
    has_hint = any(h in msg_clean for h in YESNO_HINTS)
    if not has_hint:
        print(f"[YesNo] Aucun mot oui/non dans '{message}' — ignoré sans LLM")
        return None

    # 🤖 LLM pour les cas ambigus
    prompt = f"""Tu analyses une réponse vocale d'un client dans un restaurant.
Le client devait répondre OUI ou NON à une question.
Sa réponse (transcription vocale, peut contenir des erreurs) : "{message}"

ATTENTION : la transcription vocale déforme souvent les mots :
- "non" peut devenir "bon", "neu", "nee", "nein", "bonne"
- "oui" peut devenir "wui", "voui", "ouai"
- "c'est tout" ou "rien" ou "pas de" = NON

Réponds UNIQUEMENT par un seul mot : "oui" ou "non" ou "inconnu"
- "oui" si la réponse exprime accord, confirmation, acceptation
- "non" si la réponse exprime refus, négation, annulation  
- "inconnu" si impossible de déterminer

Réponse :"""

    result = _llm_call(prompt, max_tokens=5)
    print(f"[YesNo] LLM → '{result}' pour '{message}'")

    if "oui" in result:
        return "oui"
    if "non" in result:
        return "non"
    return None


# =============================
# FONCTION 2 — Interpréter la TAILLE
# =============================
def interpret_size(message: str) -> str | None:
    """
    Retourne "S", "M", "L", "XL", ou None si pas clair.
    
    ✅ CHANGEMENT CLÉ : si aucun mot de taille n'est détecté en fast,
    on appelle TOUJOURS le LLM (plus de filtre TAILLE_HINTS qui bloquait).
    
    Raison : le STT déforme énormément les mots courts comme "petit" → "laitenti",
    "moyen" → "moinne", etc. Seul le LLM peut interpréter ces déformations.
    """

    msg_upper = message.upper()

    # ✅ Détection rapide sans LLM
    FAST_SIZES = {
        "XL": ["XL", "TRES GRAND", "TRÈS GRAND", "EXTRA", "EXTRA LARGE", "EXTRA-LARGE", "MAXI"],
        "L":  ["GRAND", "GRANDE", "LARGE", " L "],
        "M":  ["MOYEN", "MOYENNE", "MEDIUM", " M ", "NORMAL", "STANDARD",
               "PATRIMOINE", "MOIENNE", "MOIN", "UN PAIN MOYENNE", "PAIN MOYENNE",
               "ILTAI MOYA", "MOYA", "COMMONDEUR"],
        "S":  ["PETIT", "PETITE", "SMALL", " S ", "PAIN", "PEIN", "PTIN", "P'TIT", "PTIT",
               "ET PETIT PAIN", "UN PAIN",
               # ✅ Nouvelles déformations STT de "petit"
               "LAITENTI", "LAITENTE", "LATENTI", "TITI", "PITI"],
    }
    for size, keywords in FAST_SIZES.items():
        for kw in keywords:
            if kw in msg_upper:
                print(f"[Size] Fast ✅ {size} — '{message}'")
                return size

    # ✅ CHANGEMENT : plus de filtre TAILLE_HINTS
    # On appelle TOUJOURS le LLM si le fast match n'a rien trouvé.
    # Le LLM est bien meilleur pour interpréter les déformations STT.
    # Le coût est ~1-2s de latence, mais c'est mieux que de rater la taille.

    print(f"[Size] Pas de fast match — appel LLM pour '{message}'")

    # 🤖 LLM pour interpréter
    prompt = f"""Tu analyses une réponse vocale d'un client dans un restaurant.
Le client devait choisir une taille parmi : petit (S), moyen (M), grand (L), très grand (XL).
Sa réponse (transcription vocale, PEUT CONTENIR DES ERREURS) : "{message}"

ATTENTION — la transcription vocale déforme souvent les mots :
- "petit" peut devenir "laitenti", "peti", "p'tit", "ptit", "piti", "paix de petit"
- "moyen" peut devenir "moinne", "moya", "moienne", "patrimoine"
- "grand" peut devenir "grant", "gran"
- Si le client dit "je veux la taille petit" ou "je veux du petit" → S
- Si le client dit "taille du milieu" ou "normal" → M
- Si la phrase ne concerne PAS une taille → "inconnu"

Réponds UNIQUEMENT par : "S" ou "M" ou "L" ou "XL" ou "inconnu"

Exemples :
"je prends la taille du milieu" → M
"donnez-moi le plus grand" → XL
"pas trop grand, normal" → M
"je choisis la grande taille" → L
"un laitenti" → S (déformation de "petit")
"oui je dis une paix de petit" → S
"je voudrais une taille petite" → S
"merci" → inconnu
"sous-titrage would" → inconnu

Réponse :"""

    result = _llm_call(prompt, max_tokens=5)
    result = result.strip().upper().replace('"', '').replace("'", "")
    print(f"[Size] LLM → '{result}' pour '{message}'")

    if result in ["S", "M", "L", "XL"]:
        return result
    return None