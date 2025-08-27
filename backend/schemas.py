from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class Message(BaseModel):
    role: str
    content: str
    sources: Optional[List[str]] = None

class ChatRequest(BaseModel):
    session_id: str
    model: str
    message: str
    enriched_message: Optional[str] = None
    stream: Optional[bool] = False
    sources: Optional[List[str]] = None

class ChatResponse(BaseModel):
    reply: str

class HistoryResponse(BaseModel):
    messages: List[Message]

class GenerateTitleRequest(BaseModel):
    session_id: str
    message: str
    model: str

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

class EditMessageRequest(BaseModel):
    message: str

class RegenerateRequest(BaseModel):
    index: int
    model: Optional[str] = None
    enriched_message: Optional[str] = None
    stream: bool = True
    sources: Optional[List[str]] = None

# Request payload for the web search enrichment endpoint.
class WebSearchRequest(BaseModel):
    prompt: str
    model: str
    messages: Optional[List[Message]] = None
    history_limit: Optional[int] = 8
    searx_url: Optional[str] = None
    engines: Optional[List[str]] = None
    
# Response payload for the web search enrichment endpoint.
class WebSearchResponse(BaseModel):
    enriched_prompt: str
    sources: List[str] = []
