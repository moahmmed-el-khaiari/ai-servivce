from fastapi import FastAPI
from app.models.chat_models import ChatRequest, ChatResponse
from app.state_machine.conversation_manager import (
    get_session,
    update_state,
    add_to_cart,
    clear_session
)
from app.state_machine.conversation_states import ConversationState
from app.services.llm_service import extract_order_intent
from app.services.summary_service import build_summary
from app.clients.order_client import create_order
from app.services.name_to_id_mapper import map_names_to_ids
from app.services.product_category_service import get_products_by_category
from fastapi.middleware.cors import CORSMiddleware

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


# =============================
# CHAT ENDPOINT
# =============================
@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):

    session = get_session(request.session_id)
    state = session["state"]

    # =========================
    # WELCOME
    # =========================
    if state == ConversationState.WELCOME:
        update_state(request.session_id, ConversationState.MAIN_MENU)
        return ChatResponse(
            reply=(
                "Bonjour 👋\n"
                "Je peux prendre votre commande en moins de 3 minutes.\n"
                "Souhaitez-vous commencer par nos menus ou nos pizzas ?"
            )
        )

    # =========================
    # ORDERING
    # =========================
    if state in [ConversationState.MAIN_MENU, ConversationState.ORDERING]:

        parsed = extract_order_intent(request.message)

        if not parsed or (not parsed.get("products") and not parsed.get("menus")):
            return ChatResponse(
                reply="Je n'ai pas compris votre commande. Pouvez-vous préciser ?"
            )

        add_to_cart(request.session_id, parsed)
        update_state(request.session_id, ConversationState.DRINK_OFFER)

        return ChatResponse(
            reply="Très bien 👍 Souhaitez-vous ajouter une boisson ?"
        )

    # =========================
    # DRINK OFFER
    # =========================
    if state == ConversationState.DRINK_OFFER:

        if is_yes(request.message):
            update_state(request.session_id, ConversationState.DRINK_SELECTION)
            return ChatResponse(reply="Quelle boisson souhaitez-vous ?")

        if is_no(request.message):
            update_state(request.session_id, ConversationState.DESSERT_OFFER)
            return ChatResponse(reply="Souhaitez-vous un dessert ?")

        return ChatResponse(reply="Veuillez répondre par oui ou non.")

    # =========================
    # DRINK SELECTION
    # =========================
    if state == ConversationState.DRINK_SELECTION:

        if is_no(request.message):
            update_state(request.session_id, ConversationState.DESSERT_OFFER)
            return ChatResponse(reply="Très bien 👍 Passons aux desserts.\nSouhaitez-vous un dessert ?")

        parsed = extract_order_intent(request.message)
        mapped_payload, not_found = map_names_to_ids(parsed)

        if not mapped_payload["products"]:

            drinks = get_products_by_category("DRINK")
            drink_list = "\n".join([f"- {d['name']}" for d in drinks if d["available"]])

            return ChatResponse(
                reply=(
                    "Désolé, cette boisson n'est pas disponible.\n"
                    f"Voici nos boissons disponibles :\n{drink_list}\n\n"
                    "Souhaitez-vous autre chose ?"
                )
            )

        # 🔥 Ajouter seulement les produits valides
        add_to_cart(request.session_id, parsed)

        update_state(request.session_id, ConversationState.DESSERT_OFFER)

        return ChatResponse(reply="Boisson ajoutée ✅\nSouhaitez-vous un dessert ?")

    # =========================
    # DESSERT OFFER
    # =========================
    if state == ConversationState.DESSERT_OFFER:

        if is_yes(request.message):
            update_state(request.session_id, ConversationState.DESSERT_SELECTION)
            return ChatResponse(reply="Quel dessert souhaitez-vous ?")

        if is_no(request.message):
            update_state(request.session_id, ConversationState.SUMMARY)
            summary = build_summary(session["cart"])
            return ChatResponse(reply=summary + "\n\nConfirmez-vous votre commande ?")

        return ChatResponse(reply="Veuillez répondre par oui ou non.")

    # =========================
    # DESSERT SELECTION
    # =========================
    if state == ConversationState.DESSERT_SELECTION:

        if is_no(request.message):
            update_state(request.session_id, ConversationState.SUMMARY)
            summary = build_summary(session["cart"])
            return ChatResponse(reply=summary + "\n\nConfirmez-vous votre commande ?")

        parsed = extract_order_intent(request.message)
        mapped_payload, not_found = map_names_to_ids(parsed)

        if not mapped_payload["products"]:

            desserts = get_products_by_category("DESSERT")
            dessert_list = "\n".join([f"- {d['name']}" for d in desserts if d["available"]])

            return ChatResponse(
                reply=(
                    "Désolé, ce dessert n'est pas disponible.\n"
                    f"Voici nos desserts disponibles :\n{dessert_list}\n\n"
                    "Souhaitez-vous autre chose ?"
                )
            )

        add_to_cart(request.session_id, parsed)
        update_state(request.session_id, ConversationState.SUMMARY)

        summary = build_summary(session["cart"])
        return ChatResponse(reply=summary + "\n\nConfirmez-vous votre commande ?")

    # =========================
    # CONFIRMATION
    # =========================
    if state == ConversationState.SUMMARY:

        if is_yes(request.message):

            try:
                mapped_payload, not_found = map_names_to_ids(session["cart"])

                if not mapped_payload["products"] and not mapped_payload["menus"]:
                    return ChatResponse(reply="Aucun produit valide trouvé.")

                order = create_order(mapped_payload)

            except Exception as e:
                print("Erreur création commande:", e)
                return ChatResponse(reply="Erreur lors de la création de la commande.")

            payment_link = f"https://restaurant.com/pay/{order.get('id')}"

            clear_session(request.session_id)

            return ChatResponse(
                reply=(
                    "Commande confirmée ✅\n"
                    f"Total: {order.get('totalAmount', 0)} €\n\n"
                    f"Paiement sécurisé ici :\n{payment_link}"
                )
            )

        if is_no(request.message):
            clear_session(request.session_id)
            return ChatResponse(reply="Commande annulée.")

        return ChatResponse(reply="Veuillez répondre par oui ou non.")

    return ChatResponse(reply="Je n'ai pas compris.")