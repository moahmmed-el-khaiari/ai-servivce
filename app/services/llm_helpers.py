"""
llm_helpers.py — Fonctions LLM pour interpréter les réponses vocales ambiguës
À ajouter dans llm_service.py ou importer depuis ce fichier
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

    # ✅ Détection rapide sans LLM pour les cas évidents
    msg = message.strip().lower()
    msg_clean = msg.replace('.', '').replace(',', '').replace('!', '').strip()

    FAST_YES = ["oui", "yes", "ok", "ouais", "bien sur", "absolument",
                "tout a fait", "affirmatif", "voila", "d accord", "daccord"]
    FAST_NO  = ["non", "no", "nan", "nope", "pas du tout",
                "negatif", "jamais", "annuler"]

    for w in FAST_YES:
        if w in msg_clean.split() or msg_clean == w:
            print(f"[YesNo] Fast ✅ OUI — '{message}'")
            return "oui"
    for w in FAST_NO:
        if w in msg_clean.split() or msg_clean == w:
            print(f"[YesNo] Fast ✅ NON — '{message}'")
            return "non"

    # ✅ Filtre — mots sans rapport avec oui/non → pas la peine d'appeler le LLM
    YESNO_HINTS = [
        "oui", "non", "yes", "no", "ok", "nan", "ouais", "bien", "sur",
        "accord", "absolument", "tout", "fait", "affirm", "negatif",
        "annul", "jamais", "pas", "voila", "parfait", "super"
    ]
    has_hint = any(h in msg_clean for h in YESNO_HINTS)
    if not has_hint:
        print(f"[YesNo] Aucun mot oui/non dans '{message}' — ignoré sans LLM")
        return None

    # 🤖 LLM pour les cas ambigus
    prompt = f"""Tu analyses une réponse vocale d'un client dans un restaurant.
Le client devait répondre OUI ou NON à une question.
Sa réponse : "{message}"

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
    return None  # inconnu → le service redemandera


# =============================
# FONCTION 2 — Interpréter la TAILLE
# =============================
def interpret_size(message: str) -> str | None:
    """
    Retourne "S", "M", "L", "XL", ou None si pas clair.
    Utilise le LLM pour comprendre les tailles exprimées naturellement.
    """

    msg_upper = message.upper()

    # ✅ Détection rapide sans LLM
    FAST_SIZES = {
        "XL": ["XL", "TRES GRAND", "TRÈS GRAND", "EXTRA", "EXTRA LARGE", "EXTRA-LARGE", "MAXI"],
        "L":  ["GRAND", "GRANDE", "LARGE", " L "],
        "M":  ["MOYEN", "MOYENNE", "MEDIUM", " M ", "NORMAL", "STANDARD"],
        # ✅ Alias STT connus — mots mal transcrits qui ressemblent à "petit"
        "S":  ["PETIT", "PETITE", "SMALL", " S ", "PAIN", "PEIN", "PTIN", "P'TIT", "PTIT"],
    }
    for size, keywords in FAST_SIZES.items():
        for kw in keywords:
            if kw in msg_upper:
                print(f"[Size] Fast ✅ {size} — '{message}'")
                return size

    # ✅ Filtre rapide — si aucun mot lié à une taille, inutile d'appeler le LLM
    TAILLE_HINTS = [
        "petit", "petite", "small", "p ",
        "moyen", "moyenne", "medium", "normal", "standard", "classique", "milieu",
        "grand", "grande", "large", "gros",
        "tres grand", "très grand", "extra", "maxi", "xl", "maximum", "plus grand",
        "s", "m", "l"
    ]
    msg_low = message.lower()
    has_hint = any(h in msg_low for h in TAILLE_HINTS)
    if not has_hint:
        print(f"[Size] Aucun mot de taille dans '{message}' — ignoré sans LLM")
        return None

    # 🤖 LLM pour les cas ambigus
    prompt = f"""Tu analyses une réponse vocale d'un client dans un restaurant.
Le client devait choisir une taille parmi : petit (S), moyen (M), grand (L), très grand (XL).
Sa réponse : "{message}"

Réponds UNIQUEMENT par une lettre : "S" ou "M" ou "L" ou "XL" ou "inconnu"
- S = petit, petite, small
- M = moyen, moyenne, medium, normal, standard, classique
- L = grand, grande, large
- XL = très grand, extra large, maxi, le plus grand

Exemples :
"je prends la taille du milieu" → M
"donnez-moi le plus grand" → XL
"pas trop grand, normal" → M
"je choisis la grande taille" → L
"pour la taille je choisis la taille grand" → L
"non non je prefere la taille petit" → S

Réponse :"""

    result = _llm_call(prompt, max_tokens=5)
    result = result.strip().upper().replace('"', '').replace("'", "")
    print(f"[Size] LLM → '{result}' pour '{message}'")

    if result in ["S", "M", "L", "XL"]:
        return result
    return None  # inconnu → le service redemandera