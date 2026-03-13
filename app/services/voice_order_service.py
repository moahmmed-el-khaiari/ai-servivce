import re
from requests.exceptions import ConnectionError as RequestsConnectionError
from app.state_machine.conversation_manager import (
    get_session, update_state, add_to_cart,
    clear_session, set_draft, set_customer_phone
)
from app.state_machine.conversation_states import ConversationState
from app.services.llm_service import extract_order_intent
from app.services.summary_service import build_summary
from app.services.name_to_id_mapper import map_names_to_ids, resolve_product_by_name
from app.clients.order_client import create_order
from app.services.llm_helpers import interpret_yes_no, interpret_size
from app.services.degraded_mode_service import (
    handle_pos_unavailable,
    handle_payment_unavailable,
    handle_incomprehensible_order,
)
from twilio.rest import Client as TwilioClient
from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER

# =============================
# Helpers
# =============================

def clean_voice_input(message: str) -> str:
    return re.sub(r'[^\w\s]', '', message).lower().strip()


# ✅ FIX #6 — Un seul appel interpret_yes_no au lieu de deux
def check_yes_no(message: str):
    """
    Retourne "oui", "non", ou None en un seul appel.
    Évite le double appel LLM de is_yes() + is_no().
    """
    return interpret_yes_no(message)


def is_valid_phone(text: str) -> bool:
    return bool(re.match(r"^\+?\d{9,15}$", text.strip()))

def size_label(size: str) -> str:
    return {"S": "petit", "M": "moyen", "L": "grand", "XL": "très grand"}.get(size, "")

# =============================
# Détection catégories via Spring Boot
# =============================

def enrich_category(product: dict):
    """Enrichit un produit avec sa catégorie depuis Spring Boot si manquante."""
    if not product.get("category"):
        resolved = resolve_product_by_name(product.get("name", ""))
        if resolved:
            product["category"] = resolved.get("category", "")

def has_drink(products: list) -> bool:
    for p in products:
        if p.get("category", "").upper() == "DRINK":
            return True
    return False

def has_dessert(products: list) -> bool:
    for p in products:
        if p.get("category", "").upper() == "DESSERT":
            return True
    return False

# =============================
# ✅ FIX #9 — Compteur de retries global pour tous les états
# =============================

def increment_retry(session: dict, key: str = "global_retries") -> int:
    """Incrémente et retourne le compteur de retries."""
    count = session.get(key, 0) + 1
    session[key] = count
    return count

def reset_retry(session: dict, key: str = "global_retries"):
    """Remet le compteur à zéro."""
    session[key] = 0

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
        parsed = extract_order_intent(message)
        if parsed and (parsed.get("products") or parsed.get("menus")):
            update_state(session_id, ConversationState.ORDERING)
            return handle_voice_order(session_id, message, phone_override)
        update_state(session_id, ConversationState.MAIN_MENU)
        return "Bonjour chez Savoria ! Que souhaitez-vous commander ?"

    # =========================
    # ORDERING
    # =========================
    if state in [ConversationState.MAIN_MENU, ConversationState.ORDERING]:

        # Scénario 4.2 — commande incompréhensible
        parsed = extract_order_intent(message)
        if not parsed or (not parsed.get("products") and not parsed.get("menus")):
            retry = session.get("ordering_retries", 0) + 1
            session["ordering_retries"] = retry
            degraded = handle_incomprehensible_order(session_id, retry)
            if degraded:
                clear_session(session_id)
                return degraded
            return "Pardon, répétez votre commande ?"

        session["ordering_retries"] = 0

        missing_sizes      = []
        validated_products = []

        for product in parsed.get("products", []):
            # Résoudre la catégorie depuis Spring Boot
            enrich_category(product)

            # ✅ Si c'est un dessert → taille forcée S, pas de question
            if product.get("category", "").upper() == "DESSERT":
                product["size"] = "S"
                validated_products.append(product)
                continue

            # Sinon → vérifier si la taille est manquante
            if not product.get("size") or product.get("size") in ["None", None]:
                missing_sizes.append(product)
            else:
                validated_products.append(product)

        # ✅ FIX #2 #3 — Sauvegarder aussi les menus du parsed pour ne pas les perdre
        parsed_menus = parsed.get("menus", [])

        if missing_sizes:
            session["pending_size_products"] = missing_sizes
            session["validated_products"]    = validated_products
            session["pending_menus"]         = parsed_menus  # ✅ FIX — garder les menus
            session["previous_state"]        = state
            update_state(session_id, ConversationState.ASK_SIZE)
            return f"Quelle taille pour le {missing_sizes[0]['name']} — petit, moyen ou grand ?"

        # Scénario 4.1 — POS indisponible
        try:
            full_order = {"products": validated_products, "menus": parsed_menus}
            mapped_payload, not_found = map_names_to_ids(full_order, session.get("customerPhone"))
        except RequestsConnectionError:
            clear_session(session_id)
            return handle_pos_unavailable(session_id)

        if not_found:
            return f"Désolé, on n'a pas : {', '.join(not_found)}."
        if not mapped_payload["products"] and not mapped_payload["menus"]:
            return "Désolé, je n'ai pas trouvé ça au menu."

        # ✅ FIX #2 #3 — Ajouter TOUS les produits validés ET les menus au panier
        add_to_cart(session_id, {"products": validated_products, "menus": parsed_menus})

        # ✅ Enrichir les catégories de tout le panier pour le check drink/dessert
        cart_products = session.get("cart", {}).get("products", [])
        for p in cart_products:
            enrich_category(p)

        already_drink   = has_drink(cart_products)
        already_dessert = has_dessert(cart_products)

        last    = validated_products[-1]
        sl      = size_label(last.get("size", ""))
        qty     = last.get("quantity", 1)
        recap   = f"Parfait, {qty} {last['name']}{' ' + sl if sl else ''}." if qty > 1 else f"Parfait, {last['name']}{' ' + sl if sl else ''}."

        # ✅ FIX #5 — Ne pas proposer boisson si déjà commandée
        if already_drink and already_dessert:
            update_state(session_id, ConversationState.ASK_PHONE)
            return f"{recap} " + handle_voice_order(session_id, "__auto__", phone_override)
        elif already_drink:
            update_state(session_id, ConversationState.DESSERT_OFFER)
            return f"{recap} Un dessert ?"
        elif already_dessert:
            update_state(session_id, ConversationState.DRINK_OFFER)
            return f"{recap} Une boisson avec ça ?"
        else:
            update_state(session_id, ConversationState.DRINK_OFFER)
            return f"{recap} Une boisson avec ça ?"

    # =========================
    # DRINK OFFER
    # =========================
    if state == ConversationState.DRINK_OFFER:
        # ✅ FIX #6 — Un seul appel au lieu de deux
        answer = check_yes_no(message)
        if answer == "oui":
            reset_retry(session)
            update_state(session_id, ConversationState.DRINK_SELECTION)
            return "Laquelle ?"
        if answer == "non":
            reset_retry(session)
            update_state(session_id, ConversationState.DESSERT_OFFER)
            return "Un dessert ?"

        # ✅ FIX #9 — Compteur de retries pour mode dégradé
        retry = increment_retry(session)
        degraded = handle_incomprehensible_order(session_id, retry)
        if degraded:
            clear_session(session_id)
            return degraded
        return "Oui ou non ?"

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
            return "Je n'ai pas compris, répétez ?"

        reset_retry(session)

        missing_sizes      = []
        validated_products = []
        for product in parsed.get("products", []):
            enrich_category(product)
            if not product.get("size") or product.get("size") in ["None", None]:
                missing_sizes.append(product)
            else:
                validated_products.append(product)

        if missing_sizes:
            session["pending_size_products"] = missing_sizes
            session["validated_products"]    = validated_products
            session["previous_state"]        = ConversationState.DRINK_SELECTION
            update_state(session_id, ConversationState.ASK_SIZE)
            return f"Quelle taille pour le {missing_sizes[0]['name']} — petit, moyen ou grand ?"

        try:
            mapped_payload, not_found = map_names_to_ids(
                {"products": validated_products, "menus": []},
                session.get("customerPhone")
            )
        except RequestsConnectionError:
            clear_session(session_id)
            return handle_pos_unavailable(session_id)

        if not_found or not mapped_payload["products"]:
            return "On n'a pas ça, autre chose ?"

        add_to_cart(session_id, {"products": validated_products, "menus": []})
        drink    = validated_products[-1]
        drink_sl = size_label(drink.get("size", ""))
        update_state(session_id, ConversationState.DESSERT_OFFER)
        return f"Super, {drink['name']}{' ' + drink_sl if drink_sl else ''} ajouté. Un dessert ?"

    # =========================
    # DESSERT OFFER
    # =========================
    if state == ConversationState.DESSERT_OFFER:
        # ✅ FIX #6 — Un seul appel au lieu de deux
        answer = check_yes_no(message)
        if answer == "oui":
            reset_retry(session)
            update_state(session_id, ConversationState.DESSERT_SELECTION)
            return "Lequel ?"
        if answer == "non":
            reset_retry(session)
            update_state(session_id, ConversationState.ASK_PHONE)
            return handle_voice_order(session_id, "__auto__", phone_override)

        # ✅ FIX #9 — Compteur de retries pour mode dégradé
        retry = increment_retry(session)
        degraded = handle_incomprehensible_order(session_id, retry)
        if degraded:
            clear_session(session_id)
            return degraded
        return "Oui ou non ?"

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
            return "Je n'ai pas compris, répétez ?"

        reset_retry(session)

        for product in parsed.get("products", []):
            product["size"] = "S"

        try:
            mapped_payload, not_found = map_names_to_ids(parsed, session.get("customerPhone"))
        except RequestsConnectionError:
            clear_session(session_id)
            return handle_pos_unavailable(session_id)

        if not_found or not mapped_payload["products"]:
            return "On n'a pas ça, autre chose ?"

        add_to_cart(session_id, parsed)
        dessert_name = parsed["products"][-1]["name"]
        update_state(session_id, ConversationState.ASK_PHONE)
        result = handle_voice_order(session_id, "__auto__", phone_override)
        return f"Super, {dessert_name} ajouté. " + result

    # =========================
    # ASK SIZE
    # =========================

    if state == ConversationState.ASK_SIZE:
        size = interpret_size(message)
        if not size:
            # ✅ FIX #9 — Compteur de retries pour mode dégradé
            retry = increment_retry(session)
            degraded = handle_incomprehensible_order(session_id, retry)
            if degraded:
                clear_session(session_id)
                return degraded
            return "Petit, moyen ou grand ?"

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
            return f"Et pour le {pending_products[0]['name']} — quelle taille ?"

        # ✅ FIX #2 #3 — Ajouter les menus sauvegardés au panier aussi
        pending_menus = session.pop("pending_menus", [])
        add_to_cart(session_id, {"products": validated_products, "menus": pending_menus})

        session.pop("pending_size_products", None)
        session.pop("validated_products", None)
        previous_state = session.get("previous_state")
        sl   = size_label(size)
        name = current_product["name"]

        # ✅ Enrichir les catégories du panier complet
        cart_products = session.get("cart", {}).get("products", [])
        for p in cart_products:
            enrich_category(p)

        already_drink   = has_drink(cart_products)
        already_dessert = has_dessert(cart_products)

        if previous_state == ConversationState.DRINK_SELECTION:
            # On vient de choisir une boisson → vérifier dessert
            if already_dessert:
                update_state(session_id, ConversationState.ASK_PHONE)
                return f"Noté, {name} en {sl}. " + handle_voice_order(session_id, "__auto__", phone_override)
            else:
                update_state(session_id, ConversationState.DESSERT_OFFER)
                return f"Noté, {name} en {sl}. Un dessert ?"
        else:
            # On vient de l'ORDERING → vérifier drink + dessert
            if already_drink and already_dessert:
                update_state(session_id, ConversationState.ASK_PHONE)
                return f"Noté, {name} en {sl}. " + handle_voice_order(session_id, "__auto__", phone_override)
            elif already_drink:
                update_state(session_id, ConversationState.DESSERT_OFFER)
                return f"Noté, {name} en {sl}. Un dessert ?"
            elif already_dessert:
                update_state(session_id, ConversationState.DRINK_OFFER)
                return f"Noté, {name} en {sl}. Une boisson avec ça ?"
            else:
                update_state(session_id, ConversationState.DRINK_OFFER)
                return f"Noté, {name} en {sl}. Une boisson avec ça ?"

    # =========================
    # ASK PHONE
    # =========================
    if state == ConversationState.ASK_PHONE:
        phone = phone_override or message
        if not is_valid_phone(phone):
            return "Numéro invalide, répétez ?"

        set_customer_phone(session_id, phone)

        try:
            draft_payload, not_found = map_names_to_ids(session["cart"], phone)
        except RequestsConnectionError:
            clear_session(session_id)
            return handle_pos_unavailable(session_id)

        if not draft_payload["products"] and not draft_payload["menus"]:
            return "Désolé, je n'ai pas trouvé ça au menu."

        set_draft(session_id, draft_payload)
        update_state(session_id, ConversationState.CONFIRMATION)
        summary = build_summary(session["cart"])
        return f"{summary} Confirmez-vous ? "

    # =========================
    # CONFIRMATION
    # =========================
    if state == ConversationState.CONFIRMATION:
        # ✅ FIX #6 — Un seul appel au lieu de deux
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
                twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                customer_phone = session.get("customerPhone")
                twilio_client.messages.create(
                    body=(
                        f"Votre commande Savoria est confirmée.\n"
                        f"Total : {total} €\n"
                        f"Paiement : {payment_link}"
                    ),
                    from_=TWILIO_WHATSAPP_NUMBER,
                    to=f"whatsapp:{customer_phone}"
                )
                print(f"[SMS] ✅ Envoyé à {customer_phone}")
            except Exception as e:
                print(f"[SMS] Erreur : {e}")

            clear_session(session_id)
            return f"Parfait ! Commande confirmée, {total} euros. Vous recevez le lien par SMS. Bonne journée !"

        if answer == "non":
            clear_session(session_id)
            return "Très bien, commande annulée. Bonne journée !"

        # ✅ FIX #9 — Compteur de retries pour mode dégradé
        retry = increment_retry(session)
        degraded = handle_incomprehensible_order(session_id, retry)
        if degraded:
            clear_session(session_id)
            return degraded
        return "Oui ou non ?"

    return "Pardon, répétez ?"