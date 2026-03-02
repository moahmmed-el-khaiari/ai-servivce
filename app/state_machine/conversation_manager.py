from app.state_machine.conversation_states import ConversationState

sessions = {}

def get_session(session_id: str):

    if session_id not in sessions:
        sessions[session_id] = {
            "state": ConversationState.WELCOME,   # ✅ Enum correct
            "customerPhone": None,               # 🔥 nouveau
            "cart": {
                "products": [],
                "menus": []
            },
            "draft_order": None,                 # 🔥 nouveau
            "awaiting_confirmation": False       # 🔥 nouveau
        }

    return sessions[session_id]


def update_state(session_id: str, new_state: ConversationState):
    sessions[session_id]["state"] = new_state


def set_customer_phone(session_id: str, phone: str):
    sessions[session_id]["customerPhone"] = phone


def add_to_cart(session_id: str, parsed_data: dict):

    if parsed_data.get("products"):
        sessions[session_id]["cart"]["products"].extend(parsed_data["products"])

    if parsed_data.get("menus"):
        sessions[session_id]["cart"]["menus"].extend(parsed_data["menus"])


def set_draft(session_id: str, order_data: dict):
    sessions[session_id]["draft_order"] = order_data
    sessions[session_id]["awaiting_confirmation"] = True


def clear_session(session_id: str):
    if session_id in sessions:
        del sessions[session_id]