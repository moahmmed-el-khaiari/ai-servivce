sessions = {}

def get_session(session_id: str):
    if session_id not in sessions:
        sessions[session_id] = {
            "draft_order": None,
            "awaiting_confirmation": False,
            "is_new": True
        }
    else:
        sessions[session_id]["is_new"] = False

    return sessions[session_id]


def set_draft(session_id: str, order_data: dict):
    sessions[session_id]["draft_order"] = order_data
    sessions[session_id]["awaiting_confirmation"] = True


def clear_session(session_id: str):
    if session_id in sessions:
        del sessions[session_id]
