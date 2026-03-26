"""
llm_helpers.py — LLM interprète OUI/NON et comprend la commande en même temps
Le LLM est le seul juge — regex uniquement pour les cas évidents (1-2 mots)
"""
import requests
from app.config import GROQ_API_KEY, GROQ_LLM_URL, GROQ_LLM_MODEL


def _build_headers():
    return {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }


def _llm_call(prompt: str, max_tokens: int = 10) -> str:
    """Appel LLM HTTP sync pur — fonctionne dans run_in_executor."""
    try:
        response = requests.post(
            GROQ_LLM_URL,
            headers=_build_headers(),
            json={
                "model": GROQ_LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": max_tokens
            },
            timeout=5
        )
        if response.status_code != 200:
            return ""
        return response.json()["choices"][0]["message"]["content"].strip().lower()
    except Exception as e:
        print(f"[LLM Helper] Erreur: {e}")
        return ""


# =============================
# FONCTION 1 — Interpreter OUI / NON
# =============================

# Mots courts évidents — pas besoin de LLM
_FAST_YES = {"oui", "yes", "ok", "ouais", "wui", "voui", "ui", "wi", "oue", "ouai",
             "affirmatif", "parfait", "exact", "daccord", "confirme", "confirmé",
             "oui confirme", "je confirme", "oui je confirme", "c est bon", "bien sur"}
_FAST_NO  = {"non", "no", "nan", "nope", "negatif", "jamais", "neu", "nee", "nein", "nah"}


def interpret_yes_no(message: str) -> str | None:
    """
    Le LLM comprend la réponse du client — oui, non, ou inconnu.
    Regex uniquement pour les réponses très courtes et évidentes (1-2 mots).
    Gère : "Oui je confirme", "Non pour aujourd'hui je veux...", "Ah oui bien sûr", etc.
    """
    msg_clean = message.strip().lower()
    msg_clean = msg_clean.replace('.', '').replace(',', '').replace('!', '').replace('?', '').strip()
    words = msg_clean.split()

    # ✅ Phrases de confirmation explicites — toujours OUI
    _CONFIRM_PHRASES = {
        "oui je confirme", "je confirme", "oui confirme",
        "ah oui je confirme", "oui bien sur", "c est bon",
        "tout a fait", "bien sur", "absolument", "evidemment",
        "oui oui", "oui bien sur je confirme",
    }
    if msg_clean in _CONFIRM_PHRASES or any(p in msg_clean for p in _CONFIRM_PHRASES):
        print(f"[YesNo] Fast OUI (confirmation) — '{message}'")
        return "oui"

    # ✅ Phrases de refus explicites — toujours NON
    _CANCEL_PHRASES = {
        "pas du tout", "surtout pas", "jamais de la vie",
        "c est tout", "non merci", "bah non",
    }
    if msg_clean in _CANCEL_PHRASES or any(p in msg_clean for p in _CANCEL_PHRASES):
        print(f"[YesNo] Fast NON (refus) — '{message}'")
        return "non"

    # ✅ Réponse très courte (1-2 mots) → regex suffit, pas de LLM
    if len(words) <= 2:
        for w in words:
            if w in _FAST_YES:
                print(f"[YesNo] Fast OUI — '{message}'")
                return "oui"
            if w in _FAST_NO:
                print(f"[YesNo] Fast NON — '{message}'")
                return "non"

    # ✅ Réponse longue → LLM décide (comprend le contexte, les déformations STT, etc.)
    prompt = f"""Tu es l'IA d'un restaurant. Un client répond à une question oui/non.
Sa réponse vocale (peut contenir des erreurs STT) : "{message}"

Exemples :
- "Oui" → oui
- "Non" → non
- "Oui je confirme" → oui
- "Non pour aujourd'hui je veux une pizza" → non
- "Oui, un Coca grand" → oui
- "Ah oui bien sûr" → oui
- "Non merci" → non
- "C'est bon" → oui
- "C'est tout" → non
- "Bon" → non
- "Wui" → oui
- "Neu" → non
- "Bah oui" → oui

Réponds UNIQUEMENT par : oui  ou  non  ou  inconnu

Réponse :"""

    result = _llm_call(prompt, max_tokens=5)
    print(f"[YesNo] LLM -> '{result}' pour '{message}'")

    if "oui" in result:
        return "oui"
    if "non" in result:
        return "non"
    return None


# =============================
# FONCTION 2 — Interpreter la TAILLE
# =============================

_FAST_SIZES = {
    "XL": ["XL", "TRES GRAND", "TRÈS GRAND", "EXTRA LARGE", "EXTRA-LARGE", "MAXI"],
    "L":  ["GRAND", "GRANDE", "LARGE"],
    "M":  ["MOYEN", "MOYENNE", "MEDIUM", "NORMAL", "STANDARD",
           "PATRIMOINE", "MOIENNE", "MOIN", "MOYA", "COMMONDEUR"],
    "S":  ["PETIT", "PETITE", "SMALL", "PEIN", "PTIN", "P'TIT", "PTIT",
           "LAITENTI", "LAITENTE", "LATENTI", "TITI", "PITI"],
}

# Tailles courtes isolées — vérifier avec espace pour éviter faux positifs
_FAST_SIZES_ISOLATED = {
    "XL": [" XL ", "XL"],
    "L":  [" L "],
    "M":  [" M "],
    "S":  [" S "],
}


def interpret_size(message: str) -> str | None:
    """
    Le LLM comprend la taille choisie par le client.
    Regex pour les cas évidents, LLM pour les déformations STT et cas ambigus.
    """
    msg_upper = " " + message.upper() + " "

    # ✅ Fast match — mots clairs
    for size, keywords in _FAST_SIZES.items():
        for kw in keywords:
            if kw in msg_upper:
                print(f"[Size] Fast {size} — '{message}'")
                return size

    # Tailles isolées (S, M, L, XL seuls)
    for size, keywords in _FAST_SIZES_ISOLATED.items():
        for kw in keywords:
            if kw in msg_upper:
                print(f"[Size] Fast isolated {size} — '{message}'")
                return size

    # ✅ LLM pour les déformations STT et cas ambigus
    SIZE_HINTS = {
        "petit", "petite", "moyen", "moyenne", "grand", "grande", "xl",
        "small", "medium", "large", "taille", "normal", "standard",
        "maxi", "extra", "gros", "grosse", "mini",
        "laitenti", "piti", "ptit", "pein", "ptin", "moya", "moin",
        "patrimoine", "commondeur", "grant", "gran",
    }
    msg_words = set(message.lower().replace(".", " ").replace(",", " ").split())
    if not msg_words.intersection(SIZE_HINTS):
        print(f"[Size] Aucun mot taille detecte dans '{message}' — ignore sans LLM")
        return None

    print(f"[Size] LLM pour '{message}'")

    prompt = f"""Tu analyses la réponse vocale d'un client qui choisit une taille.
Tailles disponibles : petit (S), moyen (M), grand (L), très grand (XL).
Réponse du client (peut contenir des erreurs STT) : "{message}"

Déformations courantes :
- "petit" → laitenti, peti, ptit, piti, pein
- "moyen" → moinne, moya, moienne, moin
- "grand" → grant, gran
- "très grand" → extra, maxi, xl

Réponds UNIQUEMENT par : S  ou  M  ou  L  ou  XL  ou  inconnu

Réponse :"""

    result = _llm_call(prompt, max_tokens=5)
    result = result.strip().upper().replace('"', '').replace("'", "")
    print(f"[Size] LLM -> '{result}' pour '{message}'")

    if result in ["S", "M", "L", "XL"]:
        return result
    return None