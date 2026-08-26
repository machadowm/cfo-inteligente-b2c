from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

class EvolutionKey(BaseModel):
    remoteJid: str
    fromMe: bool
    id: str

class EvolutionMessage(BaseModel):
    conversation: Optional[str] = None
    extendedTextMessage: Optional[Dict[str, Any]] = None
    imageMessage: Optional[Dict[str, Any]] = None

class EvolutionData(BaseModel):
    key: EvolutionKey
    message: EvolutionMessage
    messageType: str

class WebhookPayload(BaseModel):
    event: str
    data: EvolutionData
