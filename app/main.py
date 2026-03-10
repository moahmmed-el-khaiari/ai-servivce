from aiohttp import request
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import io
import re
from app.models.chat_models import ChatRequest, ChatResponse
from app.state_machine.conversation_manager import (get_session,update_state, add_to_cart,clear_session,set_draft, set_customer_phone)
from app.state_machine.conversation_states import ConversationState
from app.services.llm_service import extract_order_intent, generate_reply
from app.services.summary_service import build_summary
from app.services.name_to_id_mapper import map_names_to_ids
from app.clients.order_client import create_order
from app.services.stt_service import speech_to_text
from app.services.tts_service import text_to_speech
from app.clients.product_client import get_products_by_category
from app.routes.twilio_voice_new import router as voice_router
app = FastAPI()
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"],)
# =============================
# Helpers
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


app.include_router(voice_router)
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

         # 🔥 On teste si le message contient une commande
        parsed = extract_order_intent(request.message)

        if parsed and (parsed.get("products") or parsed.get("menus")):
            # 👉 On passe directement en ORDERING
            update_state(request.session_id, ConversationState.ORDERING)
            return chat(ChatRequest(
                session_id=request.session_id,
                message=request.message
            ))

        # Sinon on affiche message intelligent
        update_state(request.session_id, ConversationState.MAIN_MENU)

        return ChatResponse(
            reply=ai_reply(
                "welcome_auto",
                "Bonjour 👋\n\nSi vous connaissez déjà votre commande, indiquez-moi la quantité, le produit et la taille.\n\nSinon, vous pouvez me demander de voir le catalogue."
            )
        )
    # =========================
    # chose mode
    # =========================
    if state == ConversationState.CHOOSE_MODE:

        message = request.message.lower().strip()

        # choix 1 → commande directe
        if message == "1":
            update_state(request.session_id, ConversationState.MAIN_MENU)
            return ChatResponse(
                    reply=ai_reply(
                        "direct_order_selected",
                        "Très bien 😊 Donnez-moi votre commande."
                    )
)
        # choix 2 → catégories
        if message == "2" or "cat" in message or "voir" in message:
            update_state(request.session_id, ConversationState.SELECT_CATEGORY)

            return ChatResponse(
                    reply=ai_reply(
                        "show_categories",
                        "Voici nos catégories : PIZZA, DRINK, DESSERT, MENU. Quelle catégorie souhaitez-vous ?",
                        {
                            "categories": ["PIZZA", "DRINK", "DESSERT", "MENU"]
                        }
                    )
)

       # sinon on redemande le choix proprement
        return ChatResponse(
            reply=ai_reply(
                "choose_mode_again",
                "Veuillez choisir : 1️⃣ Commander directement ou 2️⃣ Voir les catégories."
            )
)
    #=========================
    # SELECT CATEGORY
    # =========================
    if state == ConversationState.SELECT_CATEGORY:

        category = request.message.upper().strip()

        products = get_products_by_category(category)

        if not products:
            return ChatResponse(
            reply=ai_reply(
                "invalid_category",
                "Catégorie invalide. Choisissez parmi : PIZZA, DRINK, DESSERT, MENU."
            )
        )

        session["category_products"] = products
        update_state(request.session_id, ConversationState.SELECT_PRODUCT_FROM_CATEGORY)

        product_names = "\n".join([p["name"] for p in products])

        return ChatResponse(
            reply=ai_reply(
                "show_products_by_category",
                f"Produits disponibles en {category} : {product_names}. Quel produit souhaitez-vous ?",
                {
                    "category": category,
                    "products": product_names
                }
            )
        )
    # ============================
    # SELECT PRODUCT FROM CATEGORY
    # ============================
    if state == ConversationState.SELECT_PRODUCT_FROM_CATEGORY:

        product_name = request.message.lower().strip()
        products = session.get("category_products", [])

        product = next(
            (p for p in products if product_name in p["name"].lower()),
            None
        )

        if not product:
            return ChatResponse(
                reply=ai_reply(
                    "product_not_found",
                    "Produit non trouvé. Veuillez choisir dans la liste."
                )
            )

        session["selected_product_from_category"] = product
        update_state(request.session_id, ConversationState.ASK_QUANTITY)

        return ChatResponse(
            reply=ai_reply(
                "ask_quantity",
                f"Combien de {product['name']} souhaitez-vous ?",
                {"product_name": product["name"]}
            )
        )
    #============================
    # ASK QUANTITY
    #============================
    if state == ConversationState.ASK_QUANTITY:

        try:
            quantity = int(request.message.strip())
            if quantity <= 0:
                raise ValueError
        except:
            return ChatResponse(
                    reply=ai_reply(
                        "invalid_quantity",
                        "Veuillez entrer une quantité valide (ex: 2)."
                    )
                )
        product = session.get("selected_product_from_category")
        if not product:
                update_state(request.session_id, ConversationState.SELECT_CATEGORY)
                return ChatResponse(reply=ai_reply(
                                        "internal_error_category",
                                        "Erreur interne. Recommençons par la catégorie."
                                    ))

        update_state(request.session_id, ConversationState.ORDERING)

        return chat(ChatRequest(
            session_id=request.session_id,
            message=f"{quantity} {product['name']}"
        ))
    # =========================
    # ORDERING
    # =========================
    if state in [ConversationState.MAIN_MENU, ConversationState.ORDERING]:

        message = request.message.lower().strip()

        # 🔥 Détection intelligente catalogue / catégorie
        category_keywords = ["catalogue", "menu", "voir", "categorie", "catégorie", "category"]
        category_values = ["pizza", "drink", "boisson", "dessert", "menu"]

        # Si user parle du catalogue OU mentionne une catégorie
        if any(word in message for word in category_keywords) or \
        (not any(char.isdigit() for char in message) and
            any(cat in message for cat in category_values)):

            update_state(request.session_id, ConversationState.SELECT_CATEGORY)

            return ChatResponse(
                reply=ai_reply(
                    "show_categories",
                    "Voici nos catégories : PIZZA, DRINK, DESSERT, MENU. Quelle catégorie souhaitez-vous ?",
                    {
                        "categories": ["PIZZA", "DRINK", "DESSERT", "MENU"]
                    }
                )
            )

        # 🔥 2️⃣ Sinon on traite comme commande directe
        parsed = extract_order_intent(request.message)

        if not parsed or (not parsed.get("products") and not parsed.get("menus")):
            return ChatResponse(
                reply=ai_reply(
                    "ordering_error",
                    "Je n'ai pas compris votre commande. Indiquez la quantité, le produit et la taille."
                )
            )

        missing_sizes = []
        validated_products = []

        # 🔥 Gestion tailles multiples
        for product in parsed.get("products", []):

            product_name = product["name"].lower()

            # 🍰 Dessert → taille automatique
            if "glac" in product_name or "dessert" in product_name:
                product["size"] = "S"
                validated_products.append(product)
                continue

            # 🍕🥤 Taille obligatoire
            if not product.get("size") or product.get("size") in ["None", None]:
                missing_sizes.append(product)
            else:
                validated_products.append(product)

        # 🔴 S'il manque des tailles
        if missing_sizes:
            session["pending_size_products"] = missing_sizes
            session["validated_products"] = validated_products
            session["previous_state"] = state

            update_state(request.session_id, ConversationState.ASK_SIZE)

            first_product = missing_sizes[0]

            return ChatResponse(
                reply=ai_reply(
                    "ask_size",
                    f"Quelle taille souhaitez-vous pour {first_product['name']} ? (S, M, L, XL)"
                )
            )

        # 🔥 Tous les produits ont taille
        full_order = {
            "products": validated_products,
            "menus": parsed.get("menus", [])
        }

        mapped_payload, not_found = map_names_to_ids(
            full_order,
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

        # 🔥 Ajout sécurisé (évite écrasement)
        for product in validated_products:
            add_to_cart(request.session_id, {
                "products": [product],
                "menus": []
            })

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

        missing_sizes = []
        validated_products = []

        # 🔥 Vérification des tailles pour chaque produit
        for product in parsed.get("products", []):

            if not product.get("size") or product.get("size") in ["None", None]:
                missing_sizes.append(product)
            else:
                validated_products.append(product)

        # 🔴 S'il manque des tailles
        if missing_sizes:

            session["pending_size_products"] = missing_sizes
            session["validated_products"] = validated_products
            session["previous_state"] = ConversationState.DRINK_SELECTION

            update_state(request.session_id, ConversationState.ASK_SIZE)

            first_product = missing_sizes[0]

            return ChatResponse(
                reply=ai_reply(
                    "ask_size",
                    f"Quelle taille souhaitez-vous pour {first_product['name']} ? (S, M, L, XL)"
                )
            )

        # 🔥 Tous les produits ont une taille → validation backend

        full_order = {
            "products": validated_products,
            "menus": []
        }

        mapped_payload, not_found = map_names_to_ids(
            full_order,
            session.get("customerPhone")
        )

        if not_found or not mapped_payload["products"]:
            return ChatResponse(
                reply=ai_reply(
                    "invalid_drink",
                    "Cette boisson n'existe pas."
                )
            )

        add_to_cart(request.session_id, full_order)
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
            return chat(ChatRequest(
                        session_id=request.session_id,
                        message="__auto__",
                        phone_override=request.phone_override
                    ))

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

        return chat(ChatRequest(
        session_id=request.session_id,
        message="__auto__",
        phone_override=request.phone_override
    ))
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

        pending_products = session.get("pending_size_products", [])
        validated_products = session.get("validated_products", [])

        if not pending_products:
            update_state(request.session_id, ConversationState.ORDERING)
            return ChatResponse(
                reply=ai_reply("internal_error", "Erreur interne.")
            )

        # 🔥 Prendre le premier produit sans taille
        current_product = pending_products.pop(0)
        current_product["size"] = size
        validated_products.append(current_product)

        # 🔁 S'il reste encore des produits sans taille
        if pending_products:
            session["pending_size_products"] = pending_products
            session["validated_products"] = validated_products

            next_product = pending_products[0]

            return ChatResponse(
                reply=ai_reply(
                    "ask_size",
                    f"Quelle taille souhaitez-vous pour {next_product['name']} ? (S, M, L, XL)"
                )
            )

        # ✅ Tous les produits ont une taille → on les ajoute UN PAR UN
        for product in validated_products:
            add_to_cart(request.session_id, {
                "products": [product],
                "menus": []
            })

        # Nettoyage session
        session.pop("pending_size_products", None)
        session.pop("validated_products", None)

        previous_state = session.get("previous_state")

        # 🔥 Retour au bon flow
        if previous_state == ConversationState.DRINK_SELECTION:
            update_state(request.session_id, ConversationState.DESSERT_OFFER)

            return ChatResponse(
                reply=ai_reply(
                    "drink_added",
                    "Boissons ajoutées. Souhaitez-vous un dessert ?"
                )
            )
        else:
            update_state(request.session_id, ConversationState.DRINK_OFFER)

            return ChatResponse(
                reply=ai_reply(
                    "size_completed",
                    "Tailles enregistrées. Souhaitez-vous ajouter une boisson ?"
                )
        )
    # =========================
    # ASK ADD MORE
    # =========================
    if state == ConversationState.ASK_ADD_MORE:

        if is_yes(request.message):
            update_state(request.session_id, ConversationState.SELECT_CATEGORY)
            return ChatResponse(
            reply=ai_reply(
                "ask_category",
                "Quelle catégorie souhaitez-vous ?"
            ))

        if is_no(request.message):
            update_state(request.session_id, ConversationState.ASK_PHONE)
            return chat(ChatRequest(
        session_id=request.session_id,
        message="__auto__",
        phone_override=request.phone_override
    ))

        return ChatResponse(reply=ai_reply(
        "yes_no_required",
        "Veuillez répondre par oui ou non."
    ))

    # =========================
    # ASK PHONE
    # =========================
    if state == ConversationState.ASK_PHONE:

        phone = request.phone_override or request.message

        if not is_valid_phone(phone):
            return ChatResponse(
                reply=ai_reply(
                    "invalid_phone",
                    "Numéro invalide. Exemple : +212600000000"
                )
            )

        set_customer_phone(request.session_id, phone)

        draft_payload, not_found = map_names_to_ids(
            session["cart"],
            phone
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