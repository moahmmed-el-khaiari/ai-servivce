def build_summary(cart: dict):

    lines = []
    total_items = 0

    # 🔹 PRODUCTS
    for p in cart.get("products", []):
        quantity = p.get("quantity", 1)
        name = p.get("name")
        size = p.get("size")

        if size:
            line = f"- {quantity} x {name} ({size})"
        else:
            line = f"- {quantity} x {name}"

        if p.get("extraSauces"):
            sauces = ", ".join(p["extraSauces"])
            line += f"\n   + Sauces: {sauces}"

        lines.append(line)
        total_items += quantity

    # 🔹 MENUS
    for m in cart.get("menus", []):
        quantity = m.get("quantity", 1)
        name = m.get("name")
        lines.append(f"- {quantity} x {name}")
        total_items += quantity

    if not lines:
        return "Votre panier est vide."

    summary_text = "\n".join(lines)
    summary_text += f"\n\nTotal articles : {total_items}"

    return summary_text