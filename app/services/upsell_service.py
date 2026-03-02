def suggest_upsell(cart: dict):

    has_drink = any(p.get("category") == "DRINK" for p in cart.get("products", []))
    has_main = any(p.get("category") == "PIZZA" for p in cart.get("products", []))

    if has_main and not has_drink:
        return "Une boisson fraîche accompagnerait parfaitement votre pizza 🍹 Souhaitez-vous en ajouter une ?"

    return None