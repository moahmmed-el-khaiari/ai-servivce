import re
import asyncio
from requests.exceptions import ConnectionError as RequestsConnectionError
from app.state_machine.conversation_manager import (
    get_session, update_state, add_to_cart,
    clear_session, set_draft, set_customer_phone
)
from app.state_machine.conversation_states import ConversationState
from app.services.llm_service import (
    extract_order_intent, extract_order_intent_async,
    generate_reply, generate_reply_async,
)
from app.services.summary_service import build_summary
from app.services.name_to_id_mapper import map_names_to_ids, resolve_product_by_name, clear_product_cache
from app.clients.order_client import create_order
from app.services.llm_helpers import interpret_yes_no, interpret_size
from app.services.degraded_mode_service import (
    handle_pos_unavailable,
    handle_payment_unavailable,
    handle_incomprehensible_order,
)
from app.services.customer_history_service import get_last_order
from app.services.schedule_service import is_open, get_hours_message
from twilio.rest import Client as TwilioClient
from app.config import (
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
    TWILIO_WHATSAPP_NUMBER, TWILIO_PHONE_NUMBER
)


# =============================
# WRAPPER ASYNC pour map_names_to_ids
# =============================
async def _map_names_async(full_order: dict, phone: str):
    """Execute map_names_to_ids dans un thread pour ne pas bloquer la boucle async."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: map_names_to_ids(full_order, phone))


async def handle_voice_order_async(session_id: str, message: str, phone_override: str = None) -> str:
    """
    Version async de handle_voice_order.
    Appelee depuis twilio_voice_new.py via run_in_executor ou directement en async.
    Parallelise extract_order_intent + map_names_to_ids pour ORDERING.
    """
    return handle_voice_order(session_id, message, phone_override)

# =============================
# Helpers
# =============================

def clean_voice_input(message: str) -> str:
    return re.sub(r'[^\w\s]', '', message).lower().strip()

def check_yes_no(message: str):
    return interpret_yes_no(message)

def is_valid_phone(text: str) -> bool:
    return bool(re.match(r"^\+?\d{9,15}$", text.strip()))

def size_label(size: str) -> str:
    return {"S": "petit", "M": "moyen", "L": "grand", "XL": "très grand"}.get(size, "")

def size_full(size: str) -> str:
    return {"S": "taille petite", "M": "taille moyenne", "L": "grande taille", "XL": "très grande taille"}.get(size, "")

def format_items_for_llm(items: list) -> list:
    result = []
    for i in items:
        name = i.get("productName", i.get("name", ""))
        if " - " in name:
            parts = name.split(" - ")
            result.append({"produit": parts[0], "taille": size_full(parts[-1])})
        else:
            result.append({"produit": name, "taille": size_full(i.get("size", ""))})
    return result

# Actions simples → fallback direct, sans LLM (économise ~1.5s)
_SIMPLE_ACTIONS = {
    "incompris_oui_non", "taille_incomprise", "numero_invalide",
    "boisson_incomprise", "dessert_incompris", "etat_inconnu",
    "commande_incomprise", "boisson_introuvable", "dessert_introuvable",
    "produit_introuvable", "panier_vide", "demander_taille_suivante",
    "client_veut_nouvelle_commande", "client_veut_boisson", "client_veut_dessert",
    "proposer_boisson", "proposer_dessert",
    "taille_notee_proposer_boisson", "taille_notee_proposer_dessert",
    "commande_annulee", "restaurant_closed",
    # ✅ Recap aussi en fallback direct — le summary est déjà bien formaté
    "recapitulatif_commande",
}

def r(context: dict) -> str:
    """Génère la réponse : LLM pour les actions riches, fallback direct pour les simples."""
    action   = context.get("action", "")
    fallback = context.get("fallback_message", "Pardon ?")
    if action in _SIMPLE_ACTIONS:
        return fallback
    try:
        reply = generate_reply(context)
        if reply and len(reply) > 3:
            return reply
    except Exception as e:
        print(f"[LLM reply] Erreur ({action}): {e}")
    return fallback

def size_full(size: str) -> str:
    return {"S": "taille petite", "M": "taille moyenne", "L": "grande taille", "XL": "très grande taille"}.get(size, "")

# Actions simples → fallback direct, pas de LLM (économise ~1.5s)
_SIMPLE_ACTIONS = {
    "incompris_oui_non", "taille_incomprise", "numero_invalide",
    "boisson_incomprise", "dessert_incompris", "etat_inconnu",
    "commande_incomprise", "boisson_introuvable", "dessert_introuvable",
    "produit_introuvable", "taille_incomprise", "panier_vide",
    "demander_taille_suivante",
}

def r(context: dict) -> str:
    """
    Génère la réponse vocale.
    - Actions simples → fallback_message direct (0ms, pas de LLM)
    - Actions riches  → LLM generate_reply (~1.5s, ton naturel)
    """
    action = context.get("action", "")
    fallback = context.get("fallback_message", "Pardon ?")

    if action in _SIMPLE_ACTIONS:
        # ✅ Pas de LLM — réponse immédiate
        return fallback

    try:
        reply = generate_reply(context)
        if reply and len(reply) > 3:
            return reply
    except Exception as e:
        print(f"[LLM reply] Erreur ({action}): {e}")

    return fallback

# =============================
# Détection catégories
# =============================

def enrich_category(product: dict):
    """Enrichit catégorie via cache — pas de nouvel appel Spring Boot."""
    if not product.get("category"):
        resolved = resolve_product_by_name(product.get("name", ""))
        if resolved:
            product["category"] = resolved.get("category", "")

def enrich_categories_from_payload(products: list, mapped_products: list):
    """✅ Enrichit catégories depuis mapped_payload — 0 appel Spring Boot."""
    name_to_cat = {
        mp.get("_name", "").lower(): mp.get("_category", "")
        for mp in mapped_products if mp.get("_category")
    }
    for p in products:
        if not p.get("category"):
            cat = name_to_cat.get(p.get("name", "").lower(), "")
            if cat:
                p["category"] = cat
            else:
                enrich_category(p)

def enrich_categories_from_payload(products: list, mapped_products: list):
    name_to_cat = {
        mp.get("_name", "").lower(): mp.get("_category", "")
        for mp in mapped_products if mp.get("_category")
    }
    for p in products:
        if not p.get("category"):
            cat = name_to_cat.get(p.get("name", "").lower(), "")
            if cat:
                p["category"] = cat
            else:
                enrich_category(p)

def has_drink(products: list) -> bool:
    return any(p.get("category", "").upper() == "DRINK" for p in products)

def has_dessert(products: list) -> bool:
    return any(p.get("category", "").upper() == "DESSERT" for p in products)

# =============================
# Compteur de retries
# =============================

def increment_retry(session: dict, key: str = "global_retries") -> int:
    count = session.get(key, 0) + 1
    session[key] = count
    return count

def reset_retry(session: dict, key: str = "global_retries"):
    session[key] = 0

# =============================
# Formater les items d'une commande pour le LLM
# =============================

def format_items_for_llm(items: list) -> list:
    """
    Convertit les items d'une commande en liste lisible pour le LLM.
    Ex: [{"name": "Tacos poulet", "size": "M", "qty": 1}, ...]
    """
    result = []
    for i in items:
        name = i.get("productName", i.get("name", ""))
        # Séparer nom et taille si format "Nom - S/M/L"
        if " - " in name:
            parts = name.split(" - ")
            product_name = parts[0]
            size = size_full(parts[-1])
        else:
            product_name = name
            size = size_full(i.get("size", ""))
        qty = i.get("quantity", 1)
        result.append({"produit": product_name, "taille": size, "quantite": qty})
    return result

# =============================
# Handler principal
# =============================

def handle_voice_order(session_id: str, message: str, phone_override: str = None) -> str:
    session = get_session(session_id)
    state   = session["state"]

    # =========================
    # WELCOME
    # =========================
    if state == ConversationState.WELCOME:
        if not is_open():
            clear_session(session_id)
            return r({
                "action": "restaurant_closed",
                "horaires": get_hours_message(),
                "fallback_message": f"Désolé, nous sommes actuellement fermés. {get_hours_message()} Bonne journée !"
            })

        last_order = get_last_order(session_id)
        if last_order:
            items = last_order.get("items", [])
            session["last_order"] = last_order
            update_state(session_id, ConversationState.REPEAT_ORDER)
            return r({
                "action": "accueil_client_connu",
                "commande_precedente": format_items_for_llm(items),
                "total_precedent": last_order.get("totalAmount"),
                "instruction": "Accueille chaleureusement ce client fidèle. Rappelle-lui sa dernière commande avec les produits et tailles de manière naturelle. Demande-lui s'il veut recommander la même chose. Termine par 'Oui ou non ?'",
                "fallback_message": f"Bonjour cher client ! Vous avez déjà commandé chez nous. Souhaitez-vous recommander la même chose ? Oui ou non ?"
            })

        parsed = extract_order_intent(message)
        if parsed and (parsed.get("products") or parsed.get("menus")):
            update_state(session_id, ConversationState.ORDERING)
            return handle_voice_order(session_id, message, phone_override)

        update_state(session_id, ConversationState.MAIN_MENU)
        return r({
            "action": "accueil_nouveau_client",
            "restaurant": "Savoria",
            "instruction": "Accueille chaleureusement le client et invite-le à commander.",
            "fallback_message": "Bonjour et bienvenue chez Savoria ! Que souhaitez-vous commander aujourd'hui ?"
        })

    # =========================
    # REPEAT ORDER
    # =========================
    if state == ConversationState.REPEAT_ORDER:
        answer = check_yes_no(message)

        if answer == "oui":
            last = session.get("last_order", {})
            products = [
                {
                    "productId":     i["productId"],
                    "quantity":      i["quantity"],
                    "size":          i["productName"].split(" - ")[-1] if " - " in i["productName"] else "M",
                    "extraSauceIds": []
                }
                for i in last.get("items", [])
            ]
            draft = {"customerPhone": session_id, "products": products, "menus": []}
            set_draft(session_id, draft)
            update_state(session_id, ConversationState.CONFIRMATION)
            total = last.get("totalAmount", "?")
            return r({
                "action": "confirmation_repeat_order",
                "commande": format_items_for_llm(last.get("items", [])),
                "total": total,
                "instruction": "Confirme que la même commande a été préparée. Donne le total. Demande confirmation finale. Termine par 'Oui ou non ?'",
                "fallback_message": f"Parfait ! Même commande pour {total} euros. Confirmez-vous ? Oui ou non ?"
            })

        if answer == "non":
            # ✅ Vérifier si le client a donné une commande dans le même message
            # Ex: "non, pour aujourd'hui je veux une pizza margherita"
            parsed = extract_order_intent(message)
            if parsed and (parsed.get("products") or parsed.get("menus")):
                update_state(session_id, ConversationState.ORDERING)
                return handle_voice_order(session_id, message, phone_override)
            update_state(session_id, ConversationState.MAIN_MENU)
            return r({
                "action": "client_veut_nouvelle_commande",
                "instruction": "Le client ne veut pas recommander la même chose. Invite-le à commander ce qu'il désire.",
                "fallback_message": "D'accord ! Que souhaitez-vous commander aujourd'hui ?"
            })

        # ✅ Client dit directement sa commande sans oui/non
        # Ex: "je veux une pizza" sans répondre à la question
        parsed = extract_order_intent(message)
        if parsed and (parsed.get("products") or parsed.get("menus")):
            update_state(session_id, ConversationState.ORDERING)
            return handle_voice_order(session_id, message, phone_override)

        retry = increment_retry(session)

        degraded = handle_incomprehensible_order(session_id, retry)
        if degraded:
            clear_session(session_id)
            return degraded
        return r({
            "action": "incompris_oui_non",
            "fallback_message": "Oui ou non ?"
        })

    # =========================
    # ORDERING
    # =========================
    if state in [ConversationState.MAIN_MENU, ConversationState.ORDERING]:

        # ✅ OPTIMISATION : extract_order_intent + map_names_to_ids en parallele
        # extract_order_intent (LLM ~2s) et map_names_to_ids (Spring Boot ~1s)
        # sont lances simultanement via asyncio.gather -> gain ~3-4s

        # Etape 1 : extract intent (bloquant ici — appelé depuis handle_voice_order sync)
        # Pour le gain maximal, appeler handle_voice_order_async depuis twilio_voice_new.py
        parsed = extract_order_intent(message)
        if not parsed or (not parsed.get("products") and not parsed.get("menus")):
            retry = session.get("ordering_retries", 0) + 1
            session["ordering_retries"] = retry
            degraded = handle_incomprehensible_order(session_id, retry)
            if degraded:
                clear_session(session_id)
                return degraded
            return r({
                "action": "commande_incomprise",
                "fallback_message": "Pardon, je n'ai pas compris. Pouvez-vous répéter votre commande ?"
            })

        session["ordering_retries"] = 0
        missing_sizes      = []
        validated_products = []

        KNOWN_DESSERTS = {"tiramisu", "cheesecake", "fondant", "mousse", "glace", "sorbet"}

        for product in parsed.get("products", []):
            enrich_category(product)
            if not product.get("category"):
                name_lower = product.get("name", "").lower()
                if any(d in name_lower for d in KNOWN_DESSERTS):
                    product["category"] = "DESSERT"
            if product.get("category", "").upper() == "DESSERT":
                product["size"] = "S"
                validated_products.append(product)
                continue
            if not product.get("size") or product.get("size") in ["None", None]:
                missing_sizes.append(product)
            else:
                validated_products.append(product)

        parsed_menus = parsed.get("menus", [])

        if missing_sizes:
            session["pending_size_products"] = missing_sizes
            session["validated_products"]    = validated_products
            session["pending_menus"]         = parsed_menus
            session["previous_state"]        = state
            update_state(session_id, ConversationState.ASK_SIZE)
            return r({
                "action": "demander_taille",
                "produit": missing_sizes[0]["name"],
                "tailles_disponibles": ["petit", "moyen", "grand"],
                "fallback_message": f"Quelle taille pour le {missing_sizes[0]['name']} — petit, moyen ou grand ?"
            })

        try:
            full_order = {"products": validated_products, "menus": parsed_menus}
            mapped_payload, not_found = map_names_to_ids(full_order, session.get("customerPhone"))
        except RequestsConnectionError:
            clear_session(session_id)
            return handle_pos_unavailable(session_id)

        if not mapped_payload["products"] and not mapped_payload["menus"]:
            return r({
                "action": "produit_introuvable",
                "fallback_message": "Désolé, je n'ai pas trouvé ce produit au menu."
            })

        validated_products_resolved = validated_products[:]
        add_to_cart(session_id, {"products": validated_products_resolved, "menus": parsed_menus})

        cart_products = session.get("cart", {}).get("products", [])
        enrich_categories_from_payload(cart_products, mapped_payload["products"])

        already_drink   = has_drink(cart_products)
        already_dessert = has_dessert(cart_products)

        found_products = [p for p in validated_products_resolved
                         if not any(nf.lower() in p["name"].lower() for nf in not_found)]
        recap_product = found_products[-1] if found_products else validated_products_resolved[-1]
        sl  = size_label(recap_product.get("size", ""))
        qty = recap_product.get("quantity", 1)

        ctx_base = {
            "produit_ajoute": recap_product["name"],
            "taille": sl,
            "quantite": qty,
            "produits_non_trouves": not_found if not_found else None,
        }

        if already_drink and already_dessert:
            update_state(session_id, ConversationState.ASK_PHONE)
            return handle_voice_order(session_id, "__auto__", phone_override)
        elif already_drink:
            update_state(session_id, ConversationState.DESSERT_OFFER)
            return r({**ctx_base, "action": "proposer_dessert",
                      "instruction": "Confirme le produit ajouté et propose un dessert.",
                      "fallback_message": f"Parfait, {recap_product['name']} {sl} ajouté. Un dessert ?"})
        elif already_dessert:
            update_state(session_id, ConversationState.DRINK_OFFER)
            return r({**ctx_base, "action": "proposer_boisson",
                      "instruction": "Confirme le produit ajouté et propose une boisson.",
                      "fallback_message": f"Parfait, {recap_product['name']} {sl} ajouté. Une boisson avec ça ?"})
        else:
            update_state(session_id, ConversationState.DRINK_OFFER)
            return r({**ctx_base, "action": "proposer_boisson",
                      "instruction": "Confirme le produit ajouté et propose une boisson.",
                      "fallback_message": f"Parfait, {recap_product['name']} {sl} ajouté. Une boisson avec ça ?"})

    # =========================
    # DRINK OFFER
    # =========================
    if state == ConversationState.DRINK_OFFER:
        answer = check_yes_no(message)

        if answer == "oui":
            reset_retry(session)
            # ✅ Client dit "Oui, un Coca grand" → capturer directement sans redemander
            parsed_inline = extract_order_intent(message)
            if parsed_inline and parsed_inline.get("products"):
                # Traiter directement comme DRINK_SELECTION
                update_state(session_id, ConversationState.DRINK_SELECTION)
                return handle_voice_order(session_id, message, phone_override)
            # Sinon → demander quelle boisson
            update_state(session_id, ConversationState.DRINK_SELECTION)
            return r({
                "action": "client_veut_boisson",
                "instruction": "Le client veut une boisson. Demande-lui laquelle.",
                "fallback_message": "Très bien ! Quelle boisson souhaitez-vous ?"
            })

        if answer == "non":
            reset_retry(session)
            update_state(session_id, ConversationState.DESSERT_OFFER)
            return r({
                "action": "proposer_dessert",
                "instruction": "Le client ne veut pas de boisson. Propose-lui un dessert.",
                "fallback_message": "Pas de boisson, d'accord. Un dessert pour terminer ?"
            })

        # ✅ Pas de oui/non mais contient un produit → traiter comme boisson directe
        # Ex: "Un Coca moyen" sans dire "oui" d'abord
        parsed_direct = extract_order_intent(message)
        if parsed_direct and parsed_direct.get("products"):
            reset_retry(session)
            update_state(session_id, ConversationState.DRINK_SELECTION)
            return handle_voice_order(session_id, message, phone_override)

        retry = increment_retry(session)
        degraded = handle_incomprehensible_order(session_id, retry)
        if degraded:
            clear_session(session_id)
            return degraded
        return r({"action": "incompris_oui_non", "fallback_message": "Oui ou non ?"})

    # =========================
    # DRINK SELECTION
    # =========================
    if state == ConversationState.DRINK_SELECTION:
        parsed = extract_order_intent(message)
        if not parsed or not parsed.get("products"):
            retry = increment_retry(session)
            degraded = handle_incomprehensible_order(session_id, retry)
            if degraded:
                clear_session(session_id)
                return degraded
            return r({
                "action": "boisson_incomprise",
                "fallback_message": "Je n'ai pas compris, quelle boisson souhaitez-vous ?"
            })

        reset_retry(session)
        missing_sizes      = []
        validated_products = []
        KNOWN_DESSERTS = {"tiramisu", "cheesecake", "fondant", "mousse", "glace"}
        for product in parsed.get("products", []):
            enrich_category(product)
            if not product.get("category"):
                if any(d in product.get("name","").lower() for d in KNOWN_DESSERTS):
                    product["category"] = "DESSERT"
            if not product.get("size") or product.get("size") in ["None", None]:
                missing_sizes.append(product)
            else:
                validated_products.append(product)

        if missing_sizes:
            session["pending_size_products"] = missing_sizes
            session["validated_products"]    = validated_products
            session["previous_state"]        = ConversationState.DRINK_SELECTION
            update_state(session_id, ConversationState.ASK_SIZE)
            return r({
                "action": "demander_taille",
                "produit": missing_sizes[0]["name"],
                "tailles_disponibles": ["petit", "moyen", "grand"],
                "fallback_message": f"Quelle taille pour {missing_sizes[0]['name']} — petit, moyen ou grand ?"
            })

        try:
            mapped_payload, not_found = map_names_to_ids(
                {"products": validated_products, "menus": []},
                session.get("customerPhone")
            )
        except RequestsConnectionError:
            clear_session(session_id)
            return handle_pos_unavailable(session_id)

        if not_found or not mapped_payload["products"]:
            return r({
                "action": "boisson_introuvable",
                "fallback_message": "On n'a pas cette boisson. Autre chose ?"
            })

        add_to_cart(session_id, {"products": validated_products, "menus": []})
        drink    = validated_products[-1]
        drink_sl = size_label(drink.get("size", ""))
        update_state(session_id, ConversationState.DESSERT_OFFER)
        return r({
            "action": "boisson_ajoutee_proposer_dessert",
            "boisson": drink["name"],
            "taille": drink_sl,
            "instruction": "Confirme la boisson ajoutée et propose un dessert.",
            "fallback_message": f"Super, {drink['name']} {drink_sl} ajouté. Un dessert ?"
        })

    # =========================
    # DESSERT OFFER
    # =========================
    if state == ConversationState.DESSERT_OFFER:
        answer = check_yes_no(message)

        if answer == "oui":
            reset_retry(session)
            # ✅ Client dit "Oui, un Tiramisu" → capturer directement sans redemander
            parsed_inline = extract_order_intent(message)
            if parsed_inline and parsed_inline.get("products"):
                # Traiter directement comme DESSERT_SELECTION
                update_state(session_id, ConversationState.DESSERT_SELECTION)
                return handle_voice_order(session_id, message, phone_override)
            # Sinon → demander quel dessert
            update_state(session_id, ConversationState.DESSERT_SELECTION)
            return r({
                "action": "client_veut_dessert",
                "instruction": "Le client veut un dessert. Demande-lui lequel.",
                "fallback_message": "Excellent ! Quel dessert vous ferait plaisir ?"
            })

        if answer == "non":
            reset_retry(session)
            update_state(session_id, ConversationState.ASK_PHONE)
            return handle_voice_order(session_id, "__auto__", phone_override)

        # ✅ Pas de oui/non mais contient un dessert → traiter directement
        # Ex: "Un Tiramisu" sans dire "oui" d'abord
        parsed_direct = extract_order_intent(message)
        if parsed_direct and parsed_direct.get("products"):
            reset_retry(session)
            update_state(session_id, ConversationState.DESSERT_SELECTION)
            return handle_voice_order(session_id, message, phone_override)

        retry = increment_retry(session)
        degraded = handle_incomprehensible_order(session_id, retry)
        if degraded:
            clear_session(session_id)
            return degraded
        return r({"action": "incompris_oui_non", "fallback_message": "Oui ou non ?"})

    # =========================
    # DESSERT SELECTION
    # =========================
    if state == ConversationState.DESSERT_SELECTION:
        parsed = extract_order_intent(message)
        if not parsed or not parsed.get("products"):
            retry = increment_retry(session)
            degraded = handle_incomprehensible_order(session_id, retry)
            if degraded:
                clear_session(session_id)
                return degraded
            return r({
                "action": "dessert_incompris",
                "fallback_message": "Je n'ai pas compris, quel dessert souhaitez-vous ?"
            })

        reset_retry(session)
        for product in parsed.get("products", []):
            product["size"] = "S"

        try:
            mapped_payload, not_found = map_names_to_ids(parsed, session.get("customerPhone"))
        except RequestsConnectionError:
            clear_session(session_id)
            return handle_pos_unavailable(session_id)

        if not_found or not mapped_payload["products"]:
            return r({
                "action": "dessert_introuvable",
                "fallback_message": "On n'a pas ce dessert. Autre chose ?"
            })

        add_to_cart(session_id, parsed)
        dessert_name = parsed["products"][-1]["name"]
        update_state(session_id, ConversationState.ASK_PHONE)
        result = handle_voice_order(session_id, "__auto__", phone_override)
        return r({
            "action": "dessert_ajoute",
            "dessert": dessert_name,
            "suite": result,
            "instruction": "Confirme le dessert ajouté puis enchaîne avec le message suivant.",
            "fallback_message": f"Super, {dessert_name} ajouté. " + result
        })

    # =========================
    # ASK SIZE
    # =========================
    if state == ConversationState.ASK_SIZE:
        size = interpret_size(message)
        if not size:
            retry = increment_retry(session)
            degraded = handle_incomprehensible_order(session_id, retry)
            if degraded:
                clear_session(session_id)
                return degraded
            return r({
                "action": "taille_incomprise",
                "fallback_message": "Petit, moyen ou grand ?"
            })

        reset_retry(session)
        pending_products   = session.get("pending_size_products", [])
        validated_products = session.get("validated_products", [])

        if not pending_products:
            update_state(session_id, ConversationState.ORDERING)
            return "Erreur interne."

        current_product = pending_products.pop(0)
        current_product["size"] = size
        validated_products.append(current_product)

        if pending_products:
            session["pending_size_products"] = pending_products
            session["validated_products"]    = validated_products
            return r({
                "action": "demander_taille_suivante",
                "produit": pending_products[0]["name"],
                "fallback_message": f"Et pour le {pending_products[0]['name']} — quelle taille ?"
            })

        pending_menus = session.pop("pending_menus", [])
        add_to_cart(session_id, {"products": validated_products, "menus": pending_menus})
        session.pop("pending_size_products", None)
        session.pop("validated_products", None)
        previous_state = session.get("previous_state")
        sl   = size_label(size)
        name = current_product["name"]

        cart_products = session.get("cart", {}).get("products", [])
        for p in cart_products:
            enrich_category(p)

        already_drink   = has_drink(cart_products)
        already_dessert = has_dessert(cart_products)

        ctx_size = {"produit": name, "taille_choisie": sl}

        if previous_state == ConversationState.DRINK_SELECTION:
            if already_dessert:
                update_state(session_id, ConversationState.ASK_PHONE)
                return handle_voice_order(session_id, "__auto__", phone_override)
            else:
                update_state(session_id, ConversationState.DESSERT_OFFER)
                return r({**ctx_size, "action": "taille_notee_proposer_dessert",
                          "fallback_message": f"Noté, {name} en {sl}. Un dessert ?"})
        else:
            if already_drink and already_dessert:
                update_state(session_id, ConversationState.ASK_PHONE)
                return handle_voice_order(session_id, "__auto__", phone_override)
            elif already_drink:
                update_state(session_id, ConversationState.DESSERT_OFFER)
                return r({**ctx_size, "action": "taille_notee_proposer_dessert",
                          "fallback_message": f"Noté, {name} en {sl}. Un dessert ?"})
            elif already_dessert:
                update_state(session_id, ConversationState.DRINK_OFFER)
                return r({**ctx_size, "action": "taille_notee_proposer_boisson",
                          "fallback_message": f"Noté, {name} en {sl}. Une boisson avec ça ?"})
            else:
                update_state(session_id, ConversationState.DRINK_OFFER)
                return r({**ctx_size, "action": "taille_notee_proposer_boisson",
                          "fallback_message": f"Noté, {name} en {sl}. Une boisson avec ça ?"})

    # =========================
    # ASK PHONE
    # =========================
    if state == ConversationState.ASK_PHONE:
        phone = phone_override or message
        if not is_valid_phone(phone):
            return r({
                "action": "numero_invalide",
                "fallback_message": "Je n'ai pas bien entendu votre numéro. Pouvez-vous répéter ?"
            })

        set_customer_phone(session_id, phone)

        try:
            draft_payload, not_found = map_names_to_ids(session["cart"], phone)
        except RequestsConnectionError:
            clear_session(session_id)
            return handle_pos_unavailable(session_id)

        if not draft_payload["products"] and not draft_payload["menus"]:
            return r({
                "action": "panier_vide",
                "fallback_message": "Désolé, je n'ai pas trouvé vos produits au menu."
            })

        for p in draft_payload["products"]:
            p.pop("_category", None)
            p.pop("_name", None)

        set_draft(session_id, draft_payload)
        update_state(session_id, ConversationState.CONFIRMATION)
        summary = build_summary(session["cart"])
        return r({
            "action": "recapitulatif_commande",
            "recapitulatif": summary,
            "instruction": "Récite le récapitulatif de la commande de manière naturelle et demande confirmation.",
            "fallback_message": f"{summary} Confirmez-vous ?"
        })

    # =========================
    # CONFIRMATION
    # =========================
    if state == ConversationState.CONFIRMATION:
        answer = check_yes_no(message)

        if answer == "oui":
            try:
                print("===== PAYLOAD VOCAL → ORDER-SERVICE =====")
                print(session["draft_order"])
                order = create_order(session["draft_order"])
            except RequestsConnectionError:
                clear_session(session_id)
                return handle_pos_unavailable(session_id)
            except Exception as e:
                print(f"[Voice Order] Erreur : {e}")
                clear_session(session_id)
                return handle_payment_unavailable(session_id)

            order_id     = order.get("id")
            total        = order.get("totalAmount", "?")
            payment_link = f"https://restaurant.com/pay/{order_id}"

            try:
                twilio_client  = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                customer_phone = session.get("customerPhone") or session_id
                clean_phone    = customer_phone.replace("whatsapp:", "")
                if not clean_phone.startswith("+"):
                    clean_phone = "+" + clean_phone
                print(f"[SMS] Envoi vers {clean_phone}")
                msg_body = (
                    f"Votre commande Savoria est confirmée.\n"
                    f"Total : {total} €\n"
                    f"Paiement : {payment_link}"
                )
                try:
                    twilio_client.messages.create(
                        body=msg_body,
                        from_=TWILIO_WHATSAPP_NUMBER,
                        to=f"whatsapp:{clean_phone}"
                    )
                    print(f"[SMS] ✅ WhatsApp envoyé à {clean_phone}")
                except Exception as wa_err:
                    print(f"[SMS] WhatsApp échoué : {wa_err}")
                    twilio_client.messages.create(
                        body=msg_body,
                        from_=TWILIO_PHONE_NUMBER,
                        to=clean_phone
                    )
                    print(f"[SMS] ✅ SMS classique envoyé à {clean_phone}")
            except Exception as e:
                print(f"[SMS] Erreur : {e}")

            clear_session(session_id)
            clear_product_cache()
            return r({
                "action": "commande_confirmee",
                "total": total,
                "lien_paiement": payment_link,
                "instruction": "Confirme la commande avec enthousiasme. Mentionne le total et que le lien de paiement a été envoyé par SMS. Souhaite une bonne journée.",
                "fallback_message": f"Parfait ! Commande confirmée pour {total} euros. Vous recevez le lien de paiement par SMS. Bonne journée !"
            })

        if answer == "non":
            # ✅ Vérifier si le client corrige sa commande plutôt qu'annuler
            # Ex: "Non. Je dis un tacos viande" → contient commande → ORDERING
            correction = extract_order_intent(message)
            if correction and (correction.get("products") or correction.get("menus")):
                update_state(session_id, ConversationState.ORDERING)
                return handle_voice_order(session_id, message, phone_override)
            clear_session(session_id)
            return r({
                "action": "commande_annulee",
                "fallback_message": "Très bien, commande annulée. Bonne journée !"
            })

        retry = increment_retry(session)
        degraded = handle_incomprehensible_order(session_id, retry)
        if degraded:
            clear_session(session_id)
            return degraded
        return r({"action": "incompris_oui_non", "fallback_message": "Oui ou non ?"})

    return r({"action": "etat_inconnu", "fallback_message": "Pardon, pouvez-vous répéter ?"})