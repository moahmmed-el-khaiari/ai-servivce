import requests
from app.config import PRODUCT_SERVICE_URL, MENU_SERVICE_URL, SAUCE_SERVICE_URL
from app.services.product_matcher import smart_match, apply_aliases
from app.services.llm_service import llm_match_products  # ✅ LLM fallback matcher

# ============================
# CACHE SESSION — évite les doubles appels Spring Boot
# ============================
_product_cache: dict = {}

def _cache_key(name: str) -> str:
    return name.strip().lower()

def clear_product_cache():
    """Vider le cache entre deux appels (appelé par clear_session)."""
    _product_cache.clear()
    _all_products_cache.clear()


# ============================
# CACHE LISTE COMPLÈTE PRODUITS
# ============================
_all_products_cache: list = []

def get_all_products() -> list:
    """Récupère tous les produits du menu depuis Spring Boot (avec cache)."""
    global _all_products_cache
    if _all_products_cache:
        return _all_products_cache
    try:
        resp = requests.get(PRODUCT_SERVICE_URL, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            _all_products_cache = data
            print(f"[LLM Matcher] ✅ {len(data)} produits chargés depuis Spring Boot")
        return _all_products_cache
    except Exception as e:
        print(f"[LLM Matcher] Erreur chargement produits: {e}")
        return []


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
# PRODUCT NAME → ID  (avec cache)
# ============================
def resolve_product_by_name(name: str):
    """
    Résout un nom de produit vers un objet produit depuis Spring Boot.
    ✅ Cache en mémoire — évite les appels dupliqués pour le même produit.
    """
    key = _cache_key(name)

    # ✅ Cache hit
    if key in _product_cache:
        cached = _product_cache[key]
        if cached is not None:
            print(f"[Resolver] Cache ✅ '{name}' → '{cached['name']}' (id={cached.get('id')})")
        return cached

    # Appliquer les alias STT
    corrected_name = apply_aliases(name)

    # Stratégie 1 : premier mot du nom corrigé
    search_word = corrected_name.split()[0] if corrected_name.split() else corrected_name
    data = safe_get(f"{PRODUCT_SERVICE_URL}/search", {"name": search_word})

    # Stratégie 2 : nom complet corrigé
    if not data and corrected_name != search_word:
        data = safe_get(f"{PRODUCT_SERVICE_URL}/search", {"name": corrected_name})

    # Stratégie 3 : premier mot original
    if not data and corrected_name != name:
        original_word = name.split()[0] if name.split() else name
        data = safe_get(f"{PRODUCT_SERVICE_URL}/search", {"name": original_word})

    if not data:
        print(f"[Resolver] Aucun résultat pour '{name}' (corrigé: '{corrected_name}')")
        _product_cache[key] = None
        return None

    if isinstance(data, dict):
        _product_cache[key] = data
        return data

    # Fuzzy match
    best = smart_match(corrected_name, data) or smart_match(name, data)

    if not best:
        print(f"[Resolver] Aucun fuzzy match pour '{name}' parmi {len(data)} candidats")
        _product_cache[key] = None
        return None

    for product in data:
        if product["name"].lower() == best.lower():
            print(f"[Resolver] '{name}' → '{product['name']}' (id={product.get('id')})")
            _product_cache[key] = product
            return product

    _product_cache[key] = None
    return None


# ============================
# MENU NAME → ID
# ============================
def resolve_menu_by_name(name: str):
    corrected_name = apply_aliases(name)
    search_word = corrected_name.split()[0] if corrected_name.split() else corrected_name

    data = safe_get(f"{MENU_SERVICE_URL}/search", {"name": search_word})

    if not data and corrected_name != search_word:
        data = safe_get(f"{MENU_SERVICE_URL}/search", {"name": corrected_name})

    if not data:
        return None

    if isinstance(data, dict):
        return data

    best = smart_match(corrected_name, data) or smart_match(name, data)

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
        data = safe_get(f"{SAUCE_SERVICE_URL}/search", {"name": name})
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
    """
    Résout les noms → IDs.
    Flux : alias STT → fuzzy match → LLM matcher (fallback final)
    """
    final_payload = {
        "customerPhone": customer_phone,
        "products": [],
        "menus": []
    }

    not_found = []

    # ── PRODUCTS ──────────────────────────────────────────────
    items_not_found = []
    for item in parsed.get("products", []):
        product = resolve_product_by_name(item["name"])
        if product:
            final_payload["products"].append({
                "productId":     product["id"],
                "quantity":      item["quantity"],
                "size":          item.get("size", "M"),
                "extraSauceIds": resolve_sauce_ids(item.get("extraSauces", [])),
                "_category":     product.get("category", ""),
                "_name":         product["name"],
            })
        else:
            items_not_found.append(item)

    # ✅ LLM Matcher — fallback pour les produits non résolus par fuzzy
    if items_not_found:
        all_products = get_all_products()
        if all_products:
            unfound_str = ", ".join(
                f"{i.get('quantity', 1)} {i['name']}"
                + (f" taille {i['size']}" if i.get("size") else "")
                for i in items_not_found
            )
            print(f"[LLM Matcher] Tentative pour : '{unfound_str}'")
            matched = llm_match_products(unfound_str, all_products)
            matched_original_names = set()

            for m in matched:
                # Retrouver le produit dans la liste complète
                product = next(
                    (p for p in all_products if p["name"].lower() == m["name"].lower()),
                    None
                )
                if product:
                    # Retrouver l'item original pour quantité/sauce
                    orig = next(
                        (i for i in items_not_found
                         if i["name"].lower() in m["name"].lower()
                         or m["name"].lower() in i["name"].lower()),
                        items_not_found[0]
                    )
                    size = m.get("size") or orig.get("size") or "M"
                    final_payload["products"].append({
                        "productId":     product["id"],
                        "quantity":      m.get("quantity", orig.get("quantity", 1)),
                        "size":          size,
                        "extraSauceIds": resolve_sauce_ids(orig.get("extraSauces", [])),
                        "_category":     product.get("category", ""),
                        "_name":         product["name"],
                    })
                    matched_original_names.add(orig["name"])
                    # ✅ Cache — stocker toutes les variantes du nom
                    _product_cache[_cache_key(orig["name"])] = product
                    # Aussi cacher le nom LLM exact et le nom corrigé
                    _product_cache[_cache_key(m["name"])] = product
                    from app.services.product_matcher import apply_aliases
                    corrected = apply_aliases(orig["name"])
                    _product_cache[_cache_key(corrected)] = product
                    print(f"[LLM Matcher] ✅ '{m['name']}' → id={product['id']}")

            # Ce que le LLM n'a pas résolu → not_found
            for i in items_not_found:
                if i["name"] not in matched_original_names:
                    not_found.append(i["name"])
        else:
            for i in items_not_found:
                not_found.append(i["name"])

    # ── MENUS ─────────────────────────────────────────────────
    for item in parsed.get("menus", []):
        menu = resolve_menu_by_name(item["name"])
        if menu:
            final_payload["menus"].append({
                "menuId":   menu["id"],
                "quantity": item["quantity"]
            })
        else:
            not_found.append(item["name"])

    return final_payload, not_found