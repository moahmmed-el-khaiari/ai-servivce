from fastapi import FastAPI
from app.models.chat_models import ChatRequest, ChatResponse
from app.state_machine.conversation_manager import (
    get_session,
    update_state,
    add_to_cart,
    clear_session
)
from app.state_machine.conversation_states import ConversationState
from app.services.llm_service import extract_order_intent , quick_extract  
from app.services.summary_service import build_summary
from app.clients.order_client import create_order
from app.services.name_to_id_mapper import map_names_to_ids
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
# 🔹 Helpers
# =============================
def is_yes(message: str):
    return message.lower().strip() in ["oui", "yes", "ok", "d'accord"]

def is_no(message: str):
    return message.lower().strip() in ["non", "no", "annuler"]


# =============================
# 🔥 CHAT ENDPOINT
# =============================
@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):

    session = get_session(request.session_id)
    state = session["state"]

    # =========================
    # 🔹 WELCOME
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
    # 🔹 ORDERING
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
    # 🔹 DRINK OFFER
    # =========================
    if state == ConversationState.DRINK_OFFER:

        if is_yes(request.message):
            update_state(request.session_id, ConversationState.DRINK_SELECTION)
            return ChatResponse(reply="Très bien 👍 Quelle boisson souhaitez-vous ?")

        if is_no(request.message):
            update_state(request.session_id, ConversationState.DESSERT_OFFER)
            return ChatResponse(reply="Souhaitez-vous un dessert ?")

        return ChatResponse(reply="Veuillez répondre par oui ou non.")

    # =========================
    # 🔹 DRINK SELECTION
    # =========================
    if state == ConversationState.DRINK_SELECTION:

        parsed = quick_extract(request.message)
        if not parsed:
            parsed = extract_order_intent(request.message)
        if parsed:
            add_to_cart(request.session_id, parsed)

        update_state(request.session_id, ConversationState.DESSERT_OFFER)

        return ChatResponse(reply="Boisson ajoutée ✅\nSouhaitez-vous un dessert ?")

    # =========================
    # 🔹 DESSERT OFFER
    # =========================
    if state == ConversationState.DESSERT_OFFER:

        if is_yes(request.message):
            update_state(request.session_id, ConversationState.DESSERT_SELECTION)
            return ChatResponse(reply="Quel dessert souhaitez-vous ?")

        if is_no(request.message):
            update_state(request.session_id, ConversationState.SUMMARY)
            session = get_session(request.session_id)
            summary = build_summary(session["cart"])
            return ChatResponse(reply=summary +"\n\nConfirmez-vous votre commande ?")

        return ChatResponse(reply="Veuillez répondre par oui ou non.")

    # =========================
    # 🔹 DESSERT SELECTION
    # =========================
    if state == ConversationState.DESSERT_SELECTION:

        parsed = quick_extract(request.message)
        if not parsed:
            parsed = extract_order_intent(request.message)

        if parsed:
            add_to_cart(request.session_id, parsed)

        update_state(request.session_id, ConversationState.SUMMARY)

        session = get_session(request.session_id)
        summary = build_summary(session["cart"])

        return ChatResponse(reply=summary + "\n\nConfirmez-vous votre commande ?")

    # =========================
    # 🔹 CONFIRMATION
    # =========================
    if state == ConversationState.SUMMARY:

        if is_yes(request.message):

            session = get_session(request.session_id)

            try:
                # 🔥 mapping name → id
                mapped_payload, not_found = map_names_to_ids(session["cart"])

                if not mapped_payload["products"] and not mapped_payload["menus"]:
                    return ChatResponse(
                        reply="Je ne trouve aucun produit valide. Merci de vérifier."
                    )

                if not_found:
                    return ChatResponse(
                        reply=f"Produit(s) non trouvé(s): {', '.join(not_found)}.\nVeuillez corriger."
                    )

                print("Payload envoyé à order-service:", mapped_payload)
                

                order = create_order(mapped_payload)
                
                
                print("CART FINAL:", session["cart"])
            except Exception as e:
                print("Erreur création commande:", e)
                return ChatResponse(
                    reply="Erreur lors de la création de la commande. Veuillez réessayer."
                )

            payment_link = f"https://restaurant.com/pay/{order.get('id')}"

            clear_session(request.session_id)
            
            return ChatResponse(
                reply=(
                    "Commande confirmée ✅\n"
                    f"Total: {order.get('totalAmount', 0)} €\n\n"
                    "Paiement sécurisé ici :\n"
                    f"{payment_link}"
                )
            )

        if is_no(request.message):
            clear_session(request.session_id)
            return ChatResponse(reply="Commande annulée.")

        return ChatResponse(
            reply="Veuillez répondre par oui ou non pour confirmer votre commande."
        )

    return ChatResponse(reply="Je n'ai pas compris.")
