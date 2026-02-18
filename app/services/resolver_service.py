from app.clients.product_client import get_all_products, get_all_menus

def resolve_products(parsed_json):
    products_db = get_all_products()
    menus_db = get_all_menus()

    resolved = {
        "products": [],
        "menus": []
    }

    for p in parsed_json.get("products", []):
        for db in products_db:
            if p["name"].lower() in db["name"].lower():
                resolved["products"].append({
                    "productId": db["id"],
                    "quantity": p["quantity"]
                })

    for m in parsed_json.get("menus", []):
        for db in menus_db:
            if m["name"].lower() in db["name"].lower():
                resolved["menus"].append({
                    "menuId": db["id"],
                    "quantity": m["quantity"]
                })

    return resolved