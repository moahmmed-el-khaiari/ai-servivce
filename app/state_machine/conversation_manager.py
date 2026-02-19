from app.state_machine.conversation_states import ConversationState

sessions = {}


def get_session(session_id: str):

    if session_id not in sessions:
        sessions[session_id] = {
            "state": ConversationState.WELCOME,
            "cart": {
                "products": [],
                "menus": []
            }
        }

    return sessions[session_id]


def update_state(session_id: str, new_state):
    sessions[session_id]["state"] = new_state


def add_to_cart(session_id: str, parsed_data: dict):

    if parsed_data.get("products"):
        sessions[session_id]["cart"]["products"].extend(parsed_data["products"])

    if parsed_data.get("menus"):
        sessions[session_id]["cart"]["menus"].extend(parsed_data["menus"])


def clear_session(session_id: str):
    if session_id in sessions:
        del sessions[session_id]
