import requests
from app.config import PRODUCT_SERVICE_URL, MENU_SERVICE_URL, SAUCE_SERVICE_URL


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

    if isinstance(data, list) and len(data) > 0:
        return data[0]

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
# 🔹 SAUCE NAME → ID
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
# 🔥 MASTER RESOLVER
# ============================
def map_names_to_ids(parsed: dict, customer_phone: str):

    final_payload = {
        "customerPhone": customer_phone,
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
                "quantity": item["quantity"],
                "size": item.get("size", "M"),
                "extraSauceIds": resolve_sauce_ids(
                    item.get("extraSauces", [])
                )
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