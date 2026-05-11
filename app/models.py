from pydantic import BaseModel
from typing import Optional, List

class ChatRequest(BaseModel):
    message: str
    user_id: Optional[str] = "default_user"
    conversation_id: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    audio_response: Optional[str] = None  # Base64 encoded audio

class VoiceChatRequest(BaseModel):
    audio_base64: str
    user_id: Optional[str] = "default_user"
    
class HealthResponse(BaseModel):
    status: str
    version: str