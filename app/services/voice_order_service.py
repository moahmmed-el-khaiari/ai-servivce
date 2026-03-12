import re
from requests.exceptions import ConnectionError as RequestsConnectionError
from app.state_machine.conversation_manager import (
    get_session, update_state, add_to_cart,
    clear_session, set_draft, set_customer_phone
)
from app.state_machine.conversation_states import ConversationState
from app.services.llm_service import extract_order_intent
from app.services.summary_service import build_summary
from app.services.name_to_id_mapper import map_names_to_ids
from app.clients.order_client import create_order
from app.services.llm_helpers import interpret_yes_no, interpret_size
from app.services.degraded_mode_service import (
    handle_pos_unavailable,
    handle_ai_unavailable,
    handle_payment_unavailable,
    handle_incomprehensible_order
)
from twilio.rest import Client as TwilioClient
from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN

# =============================
# Helpers
# =============================

def clean_voice_input(message: str) -> str:
    return re.sub(r'[^\w\s]', '', message).lower().strip()

def is_yes(message: str) -> bool:
    return interpret_yes_no(message) == "oui"

def is_no(message: str) -> bool:
    return interpret_yes_no(message) == "non"

def is_valid_phone(text: str) -> bool:
    return bool(re.match(r"^\+?\d{9,15}$", text.strip()))

def size_label(size: str) -> str:
    return {"S": "petit", "M": "moyen", "L": "grand", "XL": "très grand"}.get(size, "")

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

        # Scénario 4.2 — IA ne comprend pas la commande
        parsed = extract_order_intent(message)
        if not parsed or (not parsed.get("products") and not parsed.get("menus")):
            retry = session.get("ordering_retries", 0) + 1
            session["ordering_retries"] = retry
            degraded = handle_incomprehensible_order(session_id, retry)
            if degraded:
                clear_session(session_id)
                return degraded
            return "Pardon, répétez votre commande ?"

        session["ordering_retries"] = 0  # reset si compris

        missing_sizes      = []
        validated_products = []

        for product in parsed.get("products", []):
            pname = product["name"].lower()
            if "glac" in pname or "dessert" in pname:
                product["size"] = "S"
                validated_products.append(product)
                continue
            if not product.get("size") or product.get("size") in ["None", None]:
                missing_sizes.append(product)
            else:
                validated_products.append(product)

        if missing_sizes:
            session["pending_size_products"] = missing_sizes
            session["validated_products"]    = validated_products
            session["previous_state"]        = state
            update_state(session_id, ConversationState.ASK_SIZE)
            return f"Taille pour le {missing_sizes[0]['name']} — petit, moyen ou grand ?"

        # Scénario 4.1 — POS indisponible
        try:
            full_order     = {"products": validated_products, "menus": parsed.get("menus", [])}
            mapped_payload, not_found = map_names_to_ids(full_order, session.get("customerPhone"))
        except RequestsConnectionError:
            clear_session(session_id)
            return handle_pos_unavailable(session_id)

        if not_found:
            return "Désolé, on n'a pas ça au menu."
        if not mapped_payload["products"] and not mapped_payload["menus"]:
            return "Désolé, je n'ai pas trouvé ça au menu."

        for product in validated_products:
            add_to_cart(session_id, {"products": [product], "menus": []})

        last    = validated_products[-1]
        sl      = size_label(last.get("size", ""))
        qty     = last.get("quantity", 1)
        qty_str = f"{qty} " if qty > 1 else ""
        recap   = f"Parfait, {qty_str}{last['name']}{' ' + sl if sl else ''}."
        update_state(session_id, ConversationState.DRINK_OFFER)
        return f"{recap} Une boisson avec ça ?"

    # =========================
    # DRINK OFFER
    # =========================
    if state == ConversationState.DRINK_OFFER:
        if is_yes(message):
            update_state(session_id, ConversationState.DRINK_SELECTION)
            return "Laquelle ?"
        if is_no(message):
            update_state(session_id, ConversationState.DESSERT_OFFER)
            return "Un dessert ?"
        return "Oui ou non ?"

    # =========================
    # DRINK SELECTION
    # =========================
    if state == ConversationState.DRINK_SELECTION:
        parsed = extract_order_intent(message)
        if not parsed or not parsed.get("products"):
            return "Je n'ai pas compris, répétez ?"

        missing_sizes      = []
        validated_products = []
        for product in parsed.get("products", []):
            if not product.get("size") or product.get("size") in ["None", None]:
                missing_sizes.append(product)
            else:
                validated_products.append(product)

        if missing_sizes:
            session["pending_size_products"] = missing_sizes
            session["validated_products"]    = validated_products
            session["previous_state"]        = ConversationState.DRINK_SELECTION
            update_state(session_id, ConversationState.ASK_SIZE)
            return f"Taille pour le {missing_sizes[0]['name']} — petit, moyen ou grand ?"

        # Scénario 4.1
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
        drink     = validated_products[-1]
        drink_sl  = size_label(drink.get("size", ""))
        update_state(session_id, ConversationState.DESSERT_OFFER)
        return f"Super, {drink['name']}{' ' + drink_sl if drink_sl else ''} ajouté. Un dessert ?"

    # =========================
    # DESSERT OFFER
    # =========================
    if state == ConversationState.DESSERT_OFFER:
        if is_yes(message):
            update_state(session_id, ConversationState.DESSERT_SELECTION)
            return "Lequel ?"
        if is_no(message):
            update_state(session_id, ConversationState.ASK_PHONE)
            return handle_voice_order(session_id, "__auto__", phone_override)
        return "Oui ou non ?"

    # =========================
    # DESSERT SELECTION
    # =========================
    if state == ConversationState.DESSERT_SELECTION:
        parsed = extract_order_intent(message)
        if not parsed or not parsed.get("products"):
            return "Je n'ai pas compris, répétez ?"
        for product in parsed.get("products", []):
            product["size"] = "S"

        # Scénario 4.1
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
            return "Petit, moyen ou grand ?"

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
            return f"Et pour le {pending_products[0]['name']} — petit, moyen ou grand ?"

        for product in validated_products:
            add_to_cart(session_id, {"products": [product], "menus": []})

        session.pop("pending_size_products", None)
        session.pop("validated_products", None)
        previous_state = session.get("previous_state")
        sl   = size_label(size)
        name = current_product["name"]

        if previous_state == ConversationState.DRINK_SELECTION:
            update_state(session_id, ConversationState.DESSERT_OFFER)
            return f"Noté, {name} en {sl}. Un dessert ?"
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

        # Scénario 4.1
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
        return f"{summary} Confirmez-vous ? Oui ou non ?"

    # =========================
    # CONFIRMATION
    # =========================
    if state == ConversationState.CONFIRMATION:
        if is_yes(message):
            # Scénario 4.1 — POS down sur create_order
            try:
                print("===== PAYLOAD VOCAL → ORDER-SERVICE =====")
                print(session["draft_order"])
                order = create_order(session["draft_order"])
            except RequestsConnectionError:
                clear_session(session_id)
                return handle_pos_unavailable(session_id)
            except Exception as e:
                print(f"[Voice Order] Erreur : {e}")
                # Scénario 4.3 — Paiement indisponible
                clear_session(session_id)
                return handle_payment_unavailable(session_id)

            order_id     = order.get("id")
            total        = order.get("totalAmount", "?")
            payment_link = f"https://restaurant.com/pay/{order_id}"

            # Scénario 4.3 — Erreur envoi lien paiement
            try:
                twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                twilio_client.messages.create(
                    body=(
                        f"Votre commande Savoria est confirmée.\n"
                        f"Total : {total} €\n"
                        f"Paiement : {payment_link}"
                    ),
                    from_="whatsapp:+14155238886",
                    to=f"whatsapp:{session_id}"
                )
                print(f"[SMS] ✅ Envoyé à {session_id}")
            except Exception as e:
                print(f"[SMS] Erreur : {e}")

            clear_session(session_id)
            return f"Parfait ! Commande confirmée, {total} euros. Vous recevez le lien par SMS. Bonne journée !"

        if is_no(message):
            clear_session(session_id)
            return "Très bien, commande annulée. Bonne journée !"

        return "Oui ou non ?"

    return "Pardon, répétez ?"