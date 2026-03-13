import requests
from app.config import PRODUCT_SERVICE_URL, MENU_SERVICE_URL, SAUCE_SERVICE_URL
from app.services.product_matcher import smart_match, apply_aliases

# ============================
# SAFE REQUEST
# ============================
def safe_get(url, params):
    try:
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Erreur appel service: {e}")
        return None


# ============================
# PRODUCT NAME → ID
# ============================
def resolve_product_by_name(name: str):
    """
    Résout un nom de produit (possiblement déformé par le STT) vers un objet produit
    depuis le Spring Boot product-service.
    
    Stratégie :
    1. Appliquer les alias STT (tiranissette → tiramisu)
    2. Chercher avec le premier mot du nom corrigé
    3. Si pas de résultat, chercher avec le nom complet corrigé
    4. Si pas de résultat, chercher avec le premier mot du nom original
    5. Fuzzy match sur les résultats
    """

    # ✅ FIX — Appliquer les alias STT avant la recherche
    corrected_name = apply_aliases(name)

    # ✅ Stratégie 1 : chercher avec le premier mot du nom corrigé
    search_word = corrected_name.split()[0] if corrected_name.split() else corrected_name
    data = safe_get(
        f"{PRODUCT_SERVICE_URL}/search",
        {"name": search_word}
    )

    # ✅ Stratégie 2 : si rien trouvé, essayer le nom complet corrigé
    if not data and corrected_name != search_word:
        data = safe_get(
            f"{PRODUCT_SERVICE_URL}/search",
            {"name": corrected_name}
        )

    # ✅ Stratégie 3 : si le nom corrigé diffère de l'original, essayer l'original aussi
    if not data and corrected_name != name:
        original_word = name.split()[0] if name.split() else name
        data = safe_get(
            f"{PRODUCT_SERVICE_URL}/search",
            {"name": original_word}
        )

    if not data:
        print(f"[Resolver] Aucun résultat pour '{name}' (corrigé: '{corrected_name}')")
        return None

    if isinstance(data, dict):
        return data

    # ✅ Fuzzy match avec le nom corrigé (pas l'original déformé)
    best = smart_match(corrected_name, data)

    if not best:
        # Fallback : essayer avec le nom original
        best = smart_match(name, data)

    if not best:
        print(f"[Resolver] Aucun fuzzy match pour '{name}' parmi {len(data)} candidats")
        return None

    for product in data:
        if product["name"].lower() == best.lower():
            print(f"[Resolver] '{name}' → '{product['name']}' (id={product.get('id')})")
            return product

    return None


# ============================
# MENU NAME → ID
# ============================
def resolve_menu_by_name(name: str):

    # ✅ Appliquer les alias STT aussi pour les menus
    corrected_name = apply_aliases(name)
    search_word = corrected_name.split()[0] if corrected_name.split() else corrected_name

    data = safe_get(
        f"{MENU_SERVICE_URL}/search",
        {"name": search_word}
    )

    if not data and corrected_name != search_word:
        data = safe_get(
            f"{MENU_SERVICE_URL}/search",
            {"name": corrected_name}
        )

    if not data:
        return None

    if isinstance(data, dict):
        return data

    best = smart_match(corrected_name, data)

    if not best:
        best = smart_match(name, data)

    if not best:
        return None

    for product in data:
        if product["name"] == best:
            return product

    return None


# ============================
# SAUCE NAME → ID
# ============================
def resolve_sauce_ids(sauce_names: list):

    sauce_ids = []

    for name in sauce_names:
        data = safe_get(
            f"{SAUCE_SERVICE_URL}/search",
            {"name": name}
        )

        if data:
            if isinstance(data, list) and len(data) > 0:
                sauce_ids.append(data[0]["id"])
            elif isinstance(data, dict):
                sauce_ids.append(data["id"])

    return sauce_ids


# ============================
# MASTER RESOLVER
# ============================
def map_names_to_ids(parsed: dict, customer_phone: str):

    final_payload = {
        "customerPhone": customer_phone,
        "products": [],
        "menus": []
    }

    not_found = []

    # PRODUCTS
    for item in parsed.get("products", []):

        product = resolve_product_by_name(item["name"])

        if product:

            final_payload["products"].append({
                "productId": product["id"],
                "quantity": item["quantity"],
                "size": item.get("size", "M"),
                "extraSauceIds": resolve_sauce_ids(
                    item.get("extraSauces", [])
                )
            })

        else:
            not_found.append(item["name"])

    # MENUS
    for item in parsed.get("menus", []):

        menu = resolve_menu_by_name(item["name"])

        if menu:

            final_payload["menus"].append({
                "menuId": menu["id"],
                "quantity": item["quantity"]
            })

        else:
            not_found.append(item["name"])

    return final_payload, not_found