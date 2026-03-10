import re
from app.state_machine.conversation_manager import (
    get_session, update_state, add_to_cart,
    clear_session, set_draft, set_customer_phone
)
from app.state_machine.conversation_states import ConversationState
from app.services.llm_service import extract_order_intent
from app.services.summary_service import build_summary
from app.services.name_to_id_mapper import map_names_to_ids
from app.clients.order_client import create_order
from twilio.rest import Client as TwilioClient
from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
# =============================
# Helpers voix
# =============================

def clean_voice_input(message: str) -> str:
    return re.sub(r'[^\w\s]', '', message).lower().strip()

def is_yes(message: str) -> bool:
    msg = clean_voice_input(message)
    return msg in [
        "oui", "yes", "ok", "d accord", "bien sur",
        "affirmatif", "absolument", "voila", "ouais",
        "oui oui", "tout a fait"
    ]

def is_no(message: str) -> bool:
    msg = clean_voice_input(message)
    return msg in [
        "non", "no", "annuler", "nope", "nan",
        "non merci", "pas du tout", "negatif"
    ]

def is_valid_phone(text: str) -> bool:
    return bool(re.match(r"^\+?\d{9,15}$", text.strip()))

# =============================
# Handler principal
# =============================

def handle_voice_order(session_id: str, message: str, phone_override: str = None) -> str:
    session = get_session(session_id)
    state = session["state"]

    # =========================
    # WELCOME
    # =========================
    if state == ConversationState.WELCOME:
        parsed = extract_order_intent(message)
        if parsed and (parsed.get("products") or parsed.get("menus")):
            update_state(session_id, ConversationState.ORDERING)
            return handle_voice_order(session_id, message, phone_override)

        update_state(session_id, ConversationState.MAIN_MENU)
        return (
            "Bonjour et bienvenue chez Savoria. "
            "Dites votre commande avec la quantite, le produit et la taille. "
            "Par exemple : un cafe moyen, ou deux pizzas grandes."
        )

    # =========================
    # ORDERING
    # =========================
    if state in [ConversationState.MAIN_MENU, ConversationState.ORDERING]:

        parsed = extract_order_intent(message)
        if not parsed or (not parsed.get("products") and not parsed.get("menus")):
            return (
                "Je n'ai pas compris votre commande. "
                "Dites la quantite, le produit et la taille. "
                "Par exemple : un cafe moyen."
            )

        missing_sizes = []
        validated_products = []

        for product in parsed.get("products", []):
            product_name = product["name"].lower()
            if "glac" in product_name or "dessert" in product_name:
                product["size"] = "S"
                validated_products.append(product)
                continue
            if not product.get("size") or product.get("size") in ["None", None]:
                missing_sizes.append(product)
            else:
                validated_products.append(product)

        if missing_sizes:
            session["pending_size_products"] = missing_sizes
            session["validated_products"] = validated_products
            session["previous_state"] = state
            update_state(session_id, ConversationState.ASK_SIZE)
            return f"Quelle taille pour {missing_sizes[0]['name']} ? Dites petit, moyen, grand ou tres grand."

        full_order = {"products": validated_products, "menus": parsed.get("menus", [])}
        mapped_payload, not_found = map_names_to_ids(full_order, session.get("customerPhone"))

        if not_found:
            return f"Desole, nous n'avons pas : {', '.join(not_found)}."
        if not mapped_payload["products"] and not mapped_payload["menus"]:
            return "Aucun produit valide trouve."

        for product in validated_products:
            add_to_cart(session_id, {"products": [product], "menus": []})

        update_state(session_id, ConversationState.DRINK_OFFER)
        return "Souhaitez-vous ajouter une boisson ? Dites oui ou non."

    # =========================
    # DRINK OFFER
    # =========================
    if state == ConversationState.DRINK_OFFER:
        if is_yes(message):
            update_state(session_id, ConversationState.DRINK_SELECTION)
            return "Quelle boisson souhaitez-vous ?"
        if is_no(message):
            update_state(session_id, ConversationState.DESSERT_OFFER)
            return "Souhaitez-vous un dessert ? Dites oui ou non."
        return "Dites oui ou non s'il vous plait."

    # =========================
    # DRINK SELECTION
    # =========================
    if state == ConversationState.DRINK_SELECTION:
        parsed = extract_order_intent(message)
        if not parsed or not parsed.get("products"):
            return "Je n'ai pas reconnu cette boisson. Repetez s'il vous plait."

        missing_sizes = []
        validated_products = []
        for product in parsed.get("products", []):
            if not product.get("size") or product.get("size") in ["None", None]:
                missing_sizes.append(product)
            else:
                validated_products.append(product)

        if missing_sizes:
            session["pending_size_products"] = missing_sizes
            session["validated_products"] = validated_products
            session["previous_state"] = ConversationState.DRINK_SELECTION
            update_state(session_id, ConversationState.ASK_SIZE)
            return f"Quelle taille pour {missing_sizes[0]['name']} ? Dites petit, moyen, grand ou tres grand."

        full_order = {"products": validated_products, "menus": []}
        mapped_payload, not_found = map_names_to_ids(full_order, session.get("customerPhone"))
        if not_found or not mapped_payload["products"]:
            return "Cette boisson n'existe pas."

        add_to_cart(session_id, full_order)
        update_state(session_id, ConversationState.DESSERT_OFFER)
        return "Boisson ajoutee. Souhaitez-vous un dessert ? Dites oui ou non."

    # =========================
    # DESSERT OFFER
    # =========================
    if state == ConversationState.DESSERT_OFFER:
        if is_yes(message):
            update_state(session_id, ConversationState.DESSERT_SELECTION)
            return "Quel dessert souhaitez-vous ?"
        if is_no(message):
            update_state(session_id, ConversationState.ASK_PHONE)
            return handle_voice_order(session_id, "__auto__", phone_override)
        return "Dites oui ou non s'il vous plait."

    # =========================
    # DESSERT SELECTION
    # =========================
    if state == ConversationState.DESSERT_SELECTION:
        parsed = extract_order_intent(message)
        if not parsed or not parsed.get("products"):
            return "Je n'ai pas reconnu ce dessert. Repetez s'il vous plait."
        for product in parsed.get("products", []):
            product["size"] = "S"
        mapped_payload, not_found = map_names_to_ids(parsed, session.get("customerPhone"))
        if not_found or not mapped_payload["products"]:
            return "Ce dessert n'existe pas."
        add_to_cart(session_id, parsed)
        update_state(session_id, ConversationState.ASK_PHONE)
        return handle_voice_order(session_id, "__auto__", phone_override)

    # =========================
    # ASK SIZE
    # =========================
    if state == ConversationState.ASK_SIZE:
        message_upper = message.upper()
        match = re.search(r"\b(S|M|L|XL)\b", message_upper)
        if not match:
            if any(w in message_upper for w in ["PETIT", "PETITE", "SMALL"]):
                size = "S"
            elif any(w in message_upper for w in ["MOYEN", "MOYENNE", "MEDIUM"]):
                size = "M"
            elif any(w in message_upper for w in ["TRES GRAND", "TRÈS GRAND", "EXTRA"]):
                size = "XL"
            elif any(w in message_upper for w in ["GRAND", "GRANDE", "LARGE"]):
                size = "L"
            else:
                return "Choisissez une taille. Dites petit, moyen, grand ou tres grand."
        else:
            size = match.group(1)

        pending_products = session.get("pending_size_products", [])
        validated_products = session.get("validated_products", [])

        if not pending_products:
            update_state(session_id, ConversationState.ORDERING)
            return "Erreur interne."

        current_product = pending_products.pop(0)
        current_product["size"] = size
        validated_products.append(current_product)

        if pending_products:
            session["pending_size_products"] = pending_products
            session["validated_products"] = validated_products
            return f"Quelle taille pour {pending_products[0]['name']} ? Dites petit, moyen, grand ou tres grand."

        for product in validated_products:
            add_to_cart(session_id, {"products": [product], "menus": []})

        session.pop("pending_size_products", None)
        session.pop("validated_products", None)
        previous_state = session.get("previous_state")

        if previous_state == ConversationState.DRINK_SELECTION:
            update_state(session_id, ConversationState.DESSERT_OFFER)
            return "Boisson ajoutee. Souhaitez-vous un dessert ? Dites oui ou non."
        else:
            update_state(session_id, ConversationState.DRINK_OFFER)
            return "Taille enregistree. Souhaitez-vous ajouter une boisson ? Dites oui ou non."

    # =========================
    # ASK PHONE — automatique depuis Twilio
    # =========================
    if state == ConversationState.ASK_PHONE:
        phone = phone_override or message

        if not is_valid_phone(phone):
            return "Numero de telephone invalide."

        set_customer_phone(session_id, phone)
        draft_payload, not_found = map_names_to_ids(session["cart"], phone)

        if not draft_payload["products"] and not draft_payload["menus"]:
            return "Aucun produit valide trouve."

        set_draft(session_id, draft_payload)
        update_state(session_id, ConversationState.CONFIRMATION)
        summary = build_summary(session["cart"])
        return f"{summary} Confirmez-vous votre commande ? Dites oui ou non."

   # =========================
# CONFIRMATION
# =========================
    if state == ConversationState.CONFIRMATION:
        if is_yes(message):
            try:
                print("===== PAYLOAD VOCAL → ORDER-SERVICE =====")
                print(session["draft_order"])
                order = create_order(session["draft_order"])
            except Exception as e:
                print(f"[Voice Order] Erreur order : {e}")
                return "Erreur lors de la creation de la commande. Veuillez rappeler."

            order_id = order.get("id")
            total    = order.get("totalAmount", "?")
            payment_link = f"https://restaurant.com/pay/{order_id}"

            # ✅ Envoyer SMS avec lien de paiement
            try:
                twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                twilio_client.messages.create(
                    body=(
                        f"Bonjour ! Votre commande Savoria a été confirmée.\n"
                        f"Total : {total} €\n"
                        f"Lien de paiement : {payment_link}"
                    ),
                   from_="whatsapp:+14155238886",
                    to=f"whatsapp:{session_id}"
                )
                print(f"[SMS] ✅ Envoyé à {session_id}")
            except Exception as e:
                print(f"[SMS] Erreur envoi SMS : {e}")
                # Ne pas bloquer la commande si SMS échoue

            clear_session(session_id)
            return f"Commande confirmee. Total : {total} euros. Un SMS avec le lien de paiement vous a ete envoye. Merci et bonne journee !"

        if is_no(message):
            clear_session(session_id)
            return "Commande annulee. Bonne journee."

        return "Dites oui ou non s'il vous plait."

    return "Je n'ai pas compris. Pouvez-vous repeter ?"