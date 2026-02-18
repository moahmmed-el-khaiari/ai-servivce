from fastapi import FastAPI
from app.models.chat_models import ChatRequest, ChatResponse
from app.services.llm_service import extract_order_intent
from app.services.resolver_service import resolve_products
from app.services.conversation_service import get_session, set_draft, clear_session
from app.services.order_builder import build_confirmation_text
from app.clients.order_client import create_order

app = FastAPI()

@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):

    session = get_session(request.session_id)

    # 🔥 STEP 1 — Premier message
    if session["is_new"]:
        session["is_new"] = False
        return ChatResponse(reply="Bienvenue 👋 Veuillez entrer votre commande.")

    # 🔥 STEP 2 — Confirmation
    if session["awaiting_confirmation"]:
        if "oui" in request.message.lower():

            order = create_order(session["draft_order"])

            clear_session(request.session_id)

            # 🔥 Lien paiement simulé
            payment_link = f"http://localhost:4200/payment/{order['id']}"

            return ChatResponse(
                reply=f"""
Commande créée avec succès ✅

Montant : {order['totalAmount']} €

Veuillez payer ici 👇
{payment_link}
"""
            )
        else:
            clear_session(request.session_id)
            return ChatResponse(reply="Commande annulée.")

    # 🔥 STEP 3 — Extraction LLM
    parsed = extract_order_intent(request.message)

    # 🔥 STEP 4 — Résolution IDs
    resolved = resolve_products(parsed)

    # 🔥 STEP 5 — Sauvegarde draft
    set_draft(request.session_id, resolved)

    # 🔥 STEP 6 — Message confirmation
    confirmation_text = build_confirmation_text(resolved)

    return ChatResponse(reply=confirmation_text)
