# =============================
# Format naturel pour TTS vocal
# =============================

SIZE_LABELS = {
    "S":  "petit",
    "M":  "moyen",
    "L":  "grand",
    "XL": "très grand",
}

NUMBER_WORDS = {
    1: "une", 2: "deux", 3: "trois", 4: "quatre", 5: "cinq",
    6: "six",  7: "sept", 8: "huit",  9: "neuf",  10: "dix",
}

def qty_to_words(qty: int) -> str:
    return NUMBER_WORDS.get(qty, str(qty))


def build_summary(cart: dict) -> str:
    """
    Format naturel pour lecture TTS.
    Exemple : "Vous avez une pizza margherita moyenne,
               et un coca-cola petit. C'est bien ça ?"
    """
    parts = []

    for p in cart.get("products", []):
        qty   = p.get("quantity", 1)
        name  = p.get("name", "")
        size  = SIZE_LABELS.get(p.get("size", ""), "")

        qty_word = qty_to_words(qty)

        # "une pizza margherita moyenne"
        part = f"{qty_word} {name}"
        if size:
            part += f" {size}"

        parts.append(part)

    for m in cart.get("menus", []):
        qty      = m.get("quantity", 1)
        name     = m.get("name", "")
        qty_word = qty_to_words(qty)
        parts.append(f"{qty_word} menu {name}")

    if not parts:
        return "Votre panier est vide."

    # Assembler naturellement
    if len(parts) == 1:
        items_text = parts[0]
    elif len(parts) == 2:
        items_text = f"{parts[0]} et {parts[1]}"
    else:
        items_text = ", ".join(parts[:-1]) + f" et {parts[-1]}"

    return f"Vous avez {items_text}. C'est bien ça ?"


def build_confirmation_text(cart: dict) -> str:
    """Alias — même fonction, utilisé depuis voice_order_service"""
    return build_summary(cart)