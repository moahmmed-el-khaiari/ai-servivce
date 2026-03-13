# =============================
# Format naturel pour TTS vocal
# =============================

SIZE_LABELS = {
    "S":  "petit",
    "M":  "moyen",
    "L":  "grand",
    "XL": "très grand",
}

# Produits féminins — "une pizza", "une eau"
FEMININE = ["pizza", "eau", "limonade", "bière", "boisson", "tarte", "crème"]

def article(qty: int, name: str) -> str:
    """Retourne 'un' ou 'une' selon le produit, ou le chiffre si > 1"""
    if qty > 1:
        return str(qty)
    name_lower = name.lower()
    if any(f in name_lower for f in FEMININE):
        return "une"
    return "un"

def size_agree(size_label: str, name: str) -> str:
    """Accorde la taille avec le produit féminin"""
    name_lower = name.lower()
    is_fem = any(f in name_lower for f in FEMININE)
    agree = {
        "petit":     "petite"     if is_fem else "petit",
        "moyen":     "moyenne"    if is_fem else "moyen",
        "grand":     "grande"     if is_fem else "grand",
        "très grand":"très grande" if is_fem else "très grand",
    }
    return agree.get(size_label, size_label)


def build_summary(cart: dict) -> str:
    """
    Format naturel pour lecture TTS.
    Exemple : "Vous avez un café grand et un Coca-Cola moyen. Confirmez-vous ?"
    """
    parts = []

    for p in cart.get("products", []):
        qty   = p.get("quantity", 1)
        name  = p.get("name", "")
        size  = SIZE_LABELS.get(p.get("size", ""), "")

        art  = article(qty, name)
        part = f"{art} {name}"
        if size:
            part += f" {size_agree(size, name)}"
        parts.append(part)

    for m in cart.get("menus", []):
        qty  = m.get("quantity", 1)
        name = m.get("name", "")
        art  = article(qty, name)
        parts.append(f"{art} menu {name}")

    if not parts:
        return "Votre panier est vide."

    if len(parts) == 1:
        items_text = parts[0]
    elif len(parts) == 2:
        items_text = f"{parts[0]} et {parts[1]}"
    else:
        items_text = ", ".join(parts[:-1]) + f" et {parts[-1]}"

    return f"Vous avez {items_text}. Confirmez-vous ?"


def build_confirmation_text(cart: dict) -> str:
    return build_summary(cart)