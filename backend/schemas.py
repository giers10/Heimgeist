from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    session_id: str
    model: str
    message: str

class ChatResponse(BaseModel):
    reply: str

class HistoryResponse(BaseModel):
    messages: List[Message]

class GenerateTitleRequest(BaseModel):
    session_id: str
    message: str

class GenerateTitleResponse(BaseModel):
    title: str

class CreateSessionRequest(BaseModel):
    session_id: str

class ChatSession(BaseModel):
    id: int
    session_id: str
    name: str
    created_at: datetime

    class Config:
        orm_mode = True

class SessionsResponse(BaseModel):
    sessions: List[ChatSession]
