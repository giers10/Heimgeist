from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List
from . import models, schemas
from .database import Base, engine, SessionLocal
from .ollama_client import list_models as ollama_list, chat as ollama_chat

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="LLM Desktop Backend", version="0.1.0" )

# CORS (dev-friendly; tighten later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/models")
async def get_models():
    try:
        data = await ollama_list()
        return {"models": [{"name": n} for n in data.get("models", [])]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama not available: {e}")

@app.get("/sessions", response_model=schemas.SessionsResponse)
def get_sessions(db: Session = Depends(get_db)):
    sessions = db.query(models.ChatSession).order_by(models.ChatSession.created_at.desc()).all()
    return {"sessions": sessions}

@app.post("/sessions", response_model=schemas.ChatSession)
def create_session(req: schemas.CreateSessionRequest, db: Session = Depends(get_db)):
    new_session = models.ChatSession(session_id=req.session_id)
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    return new_session

@app.get("/history", response_model=schemas.HistoryResponse)
def history(session_id: str, db: Session = Depends(get_db)):
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == session_id).first()
    if not session:
        return {"messages": []}
    rows = db.query(models.ChatMessage)             .filter(models.ChatMessage.session_pk == session.id)             .order_by(models.ChatMessage.created_at.asc())             .all()
    msgs = [{"role": r.role, "content": r.content} for r in rows]
    return {"messages": msgs}

@app.post("/chat", response_model=schemas.ChatResponse)
async def chat(req: schemas.ChatRequest, db: Session = Depends(get_db)):
    # Find or create session
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == req.session_id).first()
    if not session:
        session = models.ChatSession(session_id=req.session_id)
        db.add(session)
        db.commit()
        db.refresh(session)

    # Save user message
    user_row = models.ChatMessage(session_pk=session.id, role='user', content=req.message)
    db.add(user_row)
    db.commit()

    # Build minimal conversation context (last 20 messages)
    last_msgs = db.query(models.ChatMessage)        .filter(models.ChatMessage.session_pk == session.id)        .order_by(models.ChatMessage.created_at.asc())        .all()[-20:]

    messages = [{"role": m.role, "content": m.content} for m in last_msgs]

    try:
        reply = await ollama_chat(req.model, messages)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

    # Save assistant reply
    as_row = models.ChatMessage(session_pk=session.id, role='assistant', content=reply)
    db.add(as_row)
    db.commit()

    return {"reply": reply}

@app.post("/generate-title", response_model=schemas.GenerateTitleResponse)
async def generate_title(req: schemas.GenerateTitleRequest, db: Session = Depends(get_db)):
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == req.session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    prompt = f"Generate a very short, concise title (5 words or less) for a chat conversation that begins with this user message: \"{req.message}\". Do not use quotation marks in the title."
    
    try:
        title = await ollama_chat("llama3", [{"role": "user", "content": prompt}])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

    session.name = title
    db.commit()

    return {"title": title}

@app.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)):
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete associated messages
    db.query(models.ChatMessage).filter(models.ChatMessage.session_pk == session.id).delete()
    
    db.delete(session)
    db.commit()
    return {"ok": True}

@app.put("/sessions/{session_id}/rename")
def rename_session(session_id: str, req: schemas.GenerateTitleResponse, db: Session = Depends(get_db)):
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.name = req.title
    db.commit()
    return {"ok": True}

# To run standalone: python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
