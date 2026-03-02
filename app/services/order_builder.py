def build_confirmation_text(cart: dict):

    lines = []

    for p in cart.get("products", []):
        line = f"- {p['quantity']} x {p['name']} ({p.get('size', 'M')})"

        if p.get("extraSauces"):
            sauces = ", ".join(p["extraSauces"])
            line += f"\n   + Sauces: {sauces}"

        lines.append(line)

    for m in cart.get("menus", []):
        lines.append(f"- {m['quantity']} x {m['name']}")

    if not lines:
        return "Votre panier est vide."

    text = "Voici votre commande :\n\n"
    text += "\n".join(lines)
    text += "\n\nConfirmez-vous votre commande ?"

    return text
