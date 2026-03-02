from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import io
import re

from app.models.chat_models import ChatRequest, ChatResponse
from app.state_machine.conversation_manager import (
    get_session,
    update_state,
    add_to_cart,
    clear_session,
    set_draft,
    set_customer_phone
)
from app.state_machine.conversation_states import ConversationState
from app.services.llm_service import extract_order_intent, generate_reply
from app.services.summary_service import build_summary
from app.services.name_to_id_mapper import map_names_to_ids
from app.clients.order_client import create_order
from app.services.stt_service import speech_to_text
from app.services.tts_service import text_to_speech
from app.clients.product_client import get_products_by_category

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================
# Helpers
# =============================

def is_yes(message: str):
    return message.lower().strip() in ["oui", "yes", "ok", "d'accord"]

def is_no(message: str):
    return message.lower().strip() in ["non", "no", "annuler"]

def is_valid_phone(text: str):
    pattern = r"^\+?\d{9,15}$"
    return re.match(pattern, text.strip()) is not None

def ai_reply(state: str, fallback: str, extra: dict = None):
    context = {
        "state": state,
        "fallback_message": fallback
    }
    if extra:
        context.update(extra)
    return generate_reply(context)

# =============================
# VOICE
# =============================

@app.post("/voice-chat")
async def voice_chat(file: UploadFile = File(...), session_id: str = ""):
    text = await speech_to_text(file)
    chat_response = chat(ChatRequest(session_id=session_id, message=text))
    audio_bytes = text_to_speech(chat_response.reply)

    return StreamingResponse(
        io.BytesIO(audio_bytes),
        media_type="audio/mpeg"
    )

# =============================
# CHAT
# =============================

@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):

    session = get_session(request.session_id)
    state = session["state"]

    # =========================
    # WELCOME
    # =========================
    if state == ConversationState.WELCOME:

        update_state(request.session_id, ConversationState.CHOOSE_MODE)

        return ChatResponse(
            reply="Bonjour 👋\n\nSouhaitez-vous :\n1️⃣ Commander directement (ex: 2 Margherita)\n2️⃣ Voir nos catégories ?"
        )
    # =========================
    # chose mode
    # =========================
    if state == ConversationState.CHOOSE_MODE:

        message = request.message.lower()

        # Si user veut voir catégories
        if "cat" in message or "voir" in message:
            update_state(request.session_id, ConversationState.SELECT_CATEGORY)

            return ChatResponse(
                reply="Voici nos catégories :\n- PIZZA\n- DRINK\n- DESSERT\n- MENU\n\nQuelle catégorie souhaitez-vous ?"
            )

        # Sinon on suppose commande directe
        update_state(request.session_id, ConversationState.MAIN_MENU)

        # On relance ton système actuel
        return chat(ChatRequest(
            session_id=request.session_id,
            message=request.message
        ))
    # =========================
    # SELECT CATEGORY
    # =========================
    if state == ConversationState.SELECT_CATEGORY:

        category = request.message.upper().strip()

        products = get_products_by_category(category)

        if not products:
            return ChatResponse(reply="Catégorie invalide.")

        session["category_products"] = products
        update_state(request.session_id, ConversationState.SELECT_PRODUCT_FROM_CATEGORY)

        product_names = "\n".join([p["name"] for p in products])

        return ChatResponse(
            reply=f"Produits disponibles en {category} :\n\n{product_names}\n\nQuel produit souhaitez-vous ?"
        )
    # =========================
    # SELECT PRODUCT FROM CATEGORY
    # =========================
    if state == ConversationState.SELECT_PRODUCT_FROM_CATEGORY:

        product_name = request.message.lower()
        products = session.get("category_products", [])

        product = next(
            (p for p in products if p["name"].lower() in product_name),
            None
        )

        if not product:
            return ChatResponse(reply="Produit non trouvé.")

        # On injecte dans ton système existant
        update_state(request.session_id, ConversationState.ORDERING)

        # On reformate message comme si user l'avait tapé
        return chat(ChatRequest(
            session_id=request.session_id,
            message=f"1 {product['name']}"
        ))
    # =========================
    # ORDERING
    # =========================
    if state in [ConversationState.MAIN_MENU, ConversationState.ORDERING]:

        parsed = extract_order_intent(request.message)

        if not parsed or (not parsed.get("products") and not parsed.get("menus")):
            return ChatResponse(
                reply=ai_reply(
                    "ordering_error",
                    "Je n'ai pas compris votre commande."
                )
            )

        # 🔥 Gestion tailles intelligentes
        for product in parsed.get("products", []):

            product_name = product["name"].lower()

            # 🍰 Dessert → toujours taille S
            if "glac" in product_name or "dessert" in product_name:
                product["size"] = "S"
                continue

            # 🍕🥤 Pizza / Boisson → taille obligatoire
            if not product.get("size") or product.get("size") in ["None", None]:
                session["previous_state"] = state
                session["pending_size_product"] = product
                update_state(request.session_id, ConversationState.ASK_SIZE)

                return ChatResponse(
                    reply=ai_reply(
                        "ask_size",
                        f"Quelle taille souhaitez-vous pour {product['name']} ? (S, M, L, XL)"
                    )
                )

        # 🔥 Validation backend après tailles OK
        mapped_payload, not_found = map_names_to_ids(
            parsed,
            session.get("customerPhone")
        )

        if not_found:
            return ChatResponse(
                reply=ai_reply(
                    "product_not_found",
                    f"Désolé, nous ne trouvons pas : {', '.join(not_found)}."
                )
            )

        if not mapped_payload["products"] and not mapped_payload["menus"]:
            return ChatResponse(
                reply=ai_reply(
                    "empty_cart",
                    "Aucun produit valide trouvé."
                )
            )

        add_to_cart(request.session_id, parsed)
        update_state(request.session_id, ConversationState.DRINK_OFFER)

        return ChatResponse(
            reply=ai_reply(
                "drink_offer",
                "Souhaitez-vous ajouter une boisson ?"
            )
        )

    # =========================
    # DRINK OFFER
    # =========================
    if state == ConversationState.DRINK_OFFER:

        if is_yes(request.message):
            update_state(request.session_id, ConversationState.DRINK_SELECTION)
            return ChatResponse(
                reply=ai_reply(
                    "drink_selection",
                    "Quelle boisson souhaitez-vous ?"
                )
            )

        if is_no(request.message):
            update_state(request.session_id, ConversationState.DESSERT_OFFER)
            return ChatResponse(
                reply=ai_reply(
                    "dessert_offer",
                    "Souhaitez-vous un dessert ?"
                )
            )

        return ChatResponse(
            reply=ai_reply(
                "yes_no_required",
                "Veuillez répondre par oui ou non."
            )
        )

    # =========================
    # DRINK SELECTION
    # =========================
    if state == ConversationState.DRINK_SELECTION:

        parsed = extract_order_intent(request.message)

        if not parsed.get("products"):
            return ChatResponse(
                reply=ai_reply(
                    "invalid_drink",
                    "Je n'ai pas reconnu cette boisson."
                )
            )

        # 🔥 Gestion tailles boissons
        for product in parsed.get("products", []):

            if not product.get("size") or product.get("size") in ["None", None]:
                session["previous_state"] = state
                session["pending_size_product"] = product
                update_state(request.session_id, ConversationState.ASK_SIZE)

                return ChatResponse(
                    reply=ai_reply(
                        "ask_size",
                        f"Quelle taille souhaitez-vous pour {product['name']} ? (S, M, L, XL)"
                    )
                )

        # 🔥 Validation backend après taille OK
        mapped_payload, not_found = map_names_to_ids(
            parsed,
            session.get("customerPhone")
        )

        if not_found or not mapped_payload["products"]:
            return ChatResponse(
                reply=ai_reply(
                    "invalid_drink",
                    "Cette boisson n'existe pas."
                )
            )

        add_to_cart(request.session_id, parsed)
        update_state(request.session_id, ConversationState.DESSERT_OFFER)

        return ChatResponse(
            reply=ai_reply(
                "drink_added",
                "Boisson ajoutée. Souhaitez-vous un dessert ?"
            )
        )
    # =========================
    # DESSERT OFFER
    # =========================
    if state == ConversationState.DESSERT_OFFER:

        if is_yes(request.message):
            update_state(request.session_id, ConversationState.DESSERT_SELECTION)
            return ChatResponse(
                reply=ai_reply(
                    "dessert_selection",
                    "Quel dessert souhaitez-vous ?"
                )
            )

        if is_no(request.message):
            update_state(request.session_id, ConversationState.ASK_PHONE)
            return ChatResponse(
                reply=ai_reply(
                    "ask_phone",
                    "Veuillez saisir votre numéro de téléphone."
                )
            )

        return ChatResponse(
            reply=ai_reply(
                "yes_no_required",
                "Veuillez répondre par oui ou non."
            )
        )

    # =========================
    # DESSERT SELECTION
    # =========================
    if state == ConversationState.DESSERT_SELECTION:

        parsed = extract_order_intent(request.message)

        if not parsed.get("products"):
            return ChatResponse(
                reply=ai_reply(
                    "invalid_dessert",
                    "Je n'ai pas reconnu ce dessert."
                )
            )

        # 🔥 Forcer taille S pour tous les desserts
        for product in parsed.get("products", []):
            product["size"] = "S"

        mapped_payload, not_found = map_names_to_ids(
            parsed,
            session.get("customerPhone")
        )

        if not_found or not mapped_payload["products"]:
            return ChatResponse(
                reply=ai_reply(
                    "invalid_dessert",
                    "Ce dessert n'existe pas."
                )
            )

        add_to_cart(request.session_id, parsed)
        update_state(request.session_id, ConversationState.ASK_PHONE)

        return ChatResponse(
            reply=ai_reply(
                "ask_phone",
                "Merci. Veuillez saisir votre numéro de téléphone."
            )
        )
    # =========================
    # ASK SIZE
    # =========================
    if state == ConversationState.ASK_SIZE:

        message_upper = request.message.upper()

        match = re.search(r"\b(S|M|L|XL)\b", message_upper)

        if not match:
            if "PETITE" in message_upper:
                size = "S"
            elif "MOYENNE" in message_upper:
                size = "M"
            elif "GRANDE" in message_upper:
                size = "L"
            else:
                return ChatResponse(
                    reply=ai_reply(
                        "invalid_size",
                        "Veuillez choisir une taille valide : S, M, L ou XL."
                    )
                )
        else:
            size = match.group(1)

        product = session.get("pending_size_product")

        if not product:
            update_state(request.session_id, ConversationState.ORDERING)
            return ChatResponse(reply="Erreur interne.")

        product["size"] = size

        session.pop("pending_size_product", None)

        add_to_cart(request.session_id, {"products": [product]})

        # 🔥 LOGIQUE CORRIGÉE ICI

        previous_state = session.get("previous_state")

        if previous_state == ConversationState.DRINK_SELECTION:
            update_state(request.session_id, ConversationState.DESSERT_OFFER)

            return ChatResponse(
                reply=ai_reply(
                    "drink_added",
                    f"Taille {size} ajoutée. Souhaitez-vous un dessert ?"
                )
            )

        else:
            # cas pizza
            update_state(request.session_id, ConversationState.DRINK_OFFER)

            return ChatResponse(
                reply=ai_reply(
                    "size_added",
                    f"Taille {size} ajoutée. Souhaitez-vous ajouter une boisson ?"
                )
            )
    # =========================
    # ASK ADD MORE
    # =========================
    if state == ConversationState.ASK_ADD_MORE:

        if is_yes(request.message):
            update_state(request.session_id, ConversationState.SELECT_CATEGORY)
            return ChatResponse(reply="Quelle catégorie souhaitez-vous ?")

        if is_no(request.message):
            update_state(request.session_id, ConversationState.ASK_PHONE)
            return ChatResponse(reply="Veuillez saisir votre numéro de téléphone.")

        return ChatResponse(reply="Veuillez répondre par oui ou non.")

    # =========================
    # ASK PHONE
    # =========================
    if state == ConversationState.ASK_PHONE:

        if not is_valid_phone(request.message):
            return ChatResponse(
                reply=ai_reply(
                    "invalid_phone",
                    "Numéro invalide. Exemple : +212600000000"
                )
            )

        set_customer_phone(request.session_id, request.message)

        draft_payload, not_found = map_names_to_ids(
            session["cart"],
            request.message
        )

        if not draft_payload["products"] and not draft_payload["menus"]:
            return ChatResponse(
                reply=ai_reply(
                    "empty_cart",
                    "Aucun produit valide trouvé."
                )
            )

        set_draft(request.session_id, draft_payload)
        update_state(request.session_id, ConversationState.CONFIRMATION)

        summary = build_summary(session["cart"])

        return ChatResponse(
            reply=ai_reply(
                "summary",
                summary + "\n\nConfirmez-vous votre commande ?",
                {"summary": summary}
            )
        )
    # =========================
    # CONFIRMATION
    # =========================
    if state == ConversationState.CONFIRMATION:

        if is_yes(request.message):

            try:
                print("===== PAYLOAD ENVOYÉ AU ORDER-SERVICE =====")
                print(session["draft_order"])
                print("============================================")
                order = create_order(session["draft_order"])
            except Exception:
                return ChatResponse(
                    reply=ai_reply(
                        "order_error",
                        "Erreur lors de la création de la commande."
                    )
                )

            payment_link = f"https://restaurant.com/pay/{order.get('id')}"

            clear_session(request.session_id)

            return ChatResponse(
                reply=ai_reply(
                    "order_confirmed",
                    f"Commande confirmée. Total: {order.get('totalAmount')} €. Paiement : {payment_link}",
                    {
                        "total": order.get("totalAmount"),
                        "payment_link": payment_link
                    }
                )
            )

        if is_no(request.message):
            clear_session(request.session_id)
            return ChatResponse(
                reply=ai_reply(
                    "order_cancelled",
                    "Commande annulée."
                )
            )

        return ChatResponse(
            reply=ai_reply(
                "yes_no_required",
                "Veuillez répondre par oui ou non."
            )
        )

    return ChatResponse(
        reply=ai_reply(
            "fallback",
            "Je n'ai pas compris."
        )
    )