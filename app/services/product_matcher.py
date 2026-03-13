from rapidfuzz import process, fuzz
from app.services.text_utils import normalize_text


# ✅ Alias STT — déformations courantes vues dans les logs
# Whisper déforme souvent les noms de produits étrangers ou peu courants
STT_ALIASES = {
    "tiranissette": "tiramisu",
    "tiranisu":     "tiramisu",
    "tiramissou":   "tiramisu",
    "tiramitsu":    "tiramisu",
    "tiramissu":    "tiramisu",
    "cheezcake":    "cheesecake",
    "chizcake":     "cheesecake",
    "cheesquake":   "cheesecake",
    "margarita":    "margherita",
    "marguarita":   "margherita",
    "margareta":    "margherita",
    "cocacola":     "coca-cola",
    "coca cola":    "coca-cola",
    "cocalola":     "coca-cola",
}


def apply_aliases(name: str) -> str:
    """Corrige les déformations STT connues avant le fuzzy matching."""
    normalized = normalize_text(name)
    for alias, correct in STT_ALIASES.items():
        if alias in normalized:
            print(f"[Matcher] Alias STT : '{name}' → '{correct}'")
            return correct
    return name


def smart_match(user_name: str, candidates: list):
    """
    Fuzzy matching amélioré pour les noms de produits déformés par le STT.
    
    1. Applique les alias STT connus
    2. Essaie le matching exact normalisé
    3. Fuzzy match avec score_cutoff=55 (baissé de 70 pour tolérer les déformations)
    4. Fallback: partial_ratio pour les sous-chaînes (ex: "coca" dans "coca-cola")
    """

    # ✅ Étape 1 : Corriger les déformations STT connues
    corrected_name = apply_aliases(user_name)

    normalized_user = normalize_text(corrected_name)

    normalized_candidates = {
        c["name"]: normalize_text(c["name"])
        for c in candidates
    }

    # ✅ Étape 2 : Match exact après normalisation
    for original, norm in normalized_candidates.items():
        if norm == normalized_user:
            print(f"[Matcher] Exact match : '{user_name}' → '{original}'")
            return original

    # ✅ Étape 3 : Fuzzy match principal (score_cutoff baissé de 70 à 55)
    match = process.extractOne(
        normalized_user,
        normalized_candidates.values(),
        score_cutoff=55
    )

    if match:
        matched_norm = match[0]
        score = match[1]
        for original, norm in normalized_candidates.items():
            if norm == matched_norm:
                print(f"[Matcher] Fuzzy match : '{user_name}' → '{original}' (score={score:.0f})")
                return original

    # ✅ Étape 4 : Fallback avec partial_ratio (sous-chaîne)
    # Utile quand le client dit "coca" et le produit est "coca-cola"
    best_score = 0
    best_match = None
    for original, norm in normalized_candidates.items():
        score = fuzz.partial_ratio(normalized_user, norm)
        if score > best_score:
            best_score = score
            best_match = original

    if best_match and best_score >= 70:
        print(f"[Matcher] Partial match : '{user_name}' → '{best_match}' (partial={best_score:.0f})")
        return best_match

    print(f"[Matcher] Aucun match pour '{user_name}' (normalized='{normalized_user}')")
    return None