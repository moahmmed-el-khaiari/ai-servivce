from pydantic import BaseModel
from typing import List, Optional

class ChatRequest(BaseModel):
    message: str
    session_id: str
    phone_override: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str
