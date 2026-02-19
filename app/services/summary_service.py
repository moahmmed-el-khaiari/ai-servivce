def build_summary(cart: dict):

    lines = []

    total_items = 0

    for p in cart.get("products", []):
        lines.append(f"- {p['quantity']} x {p['name']}")
        total_items += p["quantity"]

    for m in cart.get("menus", []):
        lines.append(f"- {m['quantity']} x {m['name']}")
        total_items += m["quantity"]

    if not lines:
        return "Votre panier est vide."

    summary_text = "Voici votre commande :\n\n"
    summary_text += "\n".join(lines)
    summary_text += f"\n\nTotal articles : {total_items}"

    return summary_text
