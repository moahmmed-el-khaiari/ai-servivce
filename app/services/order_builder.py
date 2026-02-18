def build_confirmation_text(resolved):
    text = "Vous souhaitez :\n"

    for p in resolved["products"]:
        text += f"- Produit ID {p['productId']} x{p['quantity']}\n"

    for m in resolved["menus"]:
        text += f"- Menu ID {m['menuId']} x{m['quantity']}\n"

    text += "\nConfirmez-vous ?"
    return text
