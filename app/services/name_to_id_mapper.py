import requests
from app.config import PRODUCT_SERVICE_URL, MENU_SERVICE_URL


# ============================
# 🔹 SAFE REQUEST
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
# 🔹 PRODUCT NAME → ID
# ============================
def resolve_product_by_name(name: str):

    data = safe_get(
        f"{PRODUCT_SERVICE_URL}/search",
        {"name": name}
    )

    if not data:
        return None

    # si API retourne liste
    if isinstance(data, list) and len(data) > 0:
        return data[0]

    # si API retourne objet direct
    if isinstance(data, dict):
        return data

    return None


# ============================
# 🔹 MENU NAME → ID
# ============================
def resolve_menu_by_name(name: str):

    data = safe_get(
        f"{MENU_SERVICE_URL}/search",
        {"name": name}
    )

    if not data:
        return None

    if isinstance(data, list) and len(data) > 0:
        return data[0]

    if isinstance(data, dict):
        return data

    return None


# ============================
# 🔥 MASTER RESOLVER
# ============================
def map_names_to_ids(parsed: dict):

    final_payload = {
        "products": [],
        "menus": []
    }

    not_found = []

    # 🔹 PRODUCTS
    for item in parsed.get("products", []):
        product = resolve_product_by_name(item["name"])

        if product:
            final_payload["products"].append({
                "productId": product["id"],
                "quantity": item["quantity"]
            })
        else:
            not_found.append(item["name"])

    # 🔹 MENUS
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
