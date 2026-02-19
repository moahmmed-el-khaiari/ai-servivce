def suggest_upsell(cart: dict):

    has_drink = any("boisson" in p["name"].lower() for p in cart.get("products", []))

    if not has_drink:
        return "Souhaitez-vous ajouter une boisson ?"

    return None
