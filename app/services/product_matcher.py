from rapidfuzz import process
from app.services.text_utils import normalize_text


def smart_match(user_name: str, candidates: list):

    normalized_user = normalize_text(user_name)

    normalized_candidates = {
        c["name"]: normalize_text(c["name"])
        for c in candidates
    }

    match = process.extractOne(
        normalized_user,
        normalized_candidates.values(),
        score_cutoff=70
    )

    if not match:
        return None

    matched_norm = match[0]

    for original, norm in normalized_candidates.items():
        if norm == matched_norm:
            return original

    return None