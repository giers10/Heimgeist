from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List
import re
import html
import json
from . import models, schemas
from .database import Base, engine, SessionLocal, ensure_sources_column
from .local_rag import router as local_rag_router
from .ollama_client import list_models as ollama_list, chat as ollama_chat, chat_stream as ollama_chat_stream
from .websearch import enrich_prompt

# Create tables + ensure migration
Base.metadata.create_all(bind=engine)
ensure_sources_column(engine)

app = FastAPI(title="LLM Desktop Backend", version="0.1.0" )

# CORS (dev-friendly; tighten later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(local_rag_router)

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
    rows = (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.session_pk == session.id)
        .order_by(models.ChatMessage.created_at.asc())
        .all()
    )
    msgs = []
    for r in rows:
        sources = []
        try:
            if getattr(r, "sources_json", None):
                sources = json.loads(r.sources_json or "[]")
        except Exception:
            sources = []
        msgs.append({"role": r.role, "content": r.content, "sources": sources})
    return {"messages": msgs}

@app.post("/chat")
async def chat(req: schemas.ChatRequest, db: Session = Depends(get_db)):
    # Find or create session
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == req.session_id).first()
    if not session:
        session = models.ChatSession(session_id=req.session_id)
        db.add(session)
        db.commit()
        db.refresh(session)

    # Store the BASE user prompt
    user_row = models.ChatMessage(session_pk=session.id, role='user', content=req.message)
    db.add(user_row)
    db.commit()

    # Build minimal context (last 20)
    last_msgs = (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.session_pk == session.id)
        .order_by(models.ChatMessage.created_at.asc())
        .all()[-20:]
    )
    messages = [{"role": m.role, "content": m.content} for m in last_msgs]

    # Patch last user with enriched_message only for LLM call
    if req.enriched_message:
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                messages = messages.copy()
                messages[i] = {**messages[i], "content": req.enriched_message}
                break

    # Sources to persist with the assistant reply
    sources = req.sources or []

    if req.stream:
        async def stream_generator():
            full_reply = ""
            try:
                async for chunk in ollama_chat_stream(req.model, messages):
                    full_reply += chunk
                    yield chunk
            except Exception as e:
                yield f"Ollama error: {e}"

            # Persist assistant reply (include sources_json)
            as_row = models.ChatMessage(
                session_pk=session.id, role='assistant', content=full_reply,
                sources_json=json.dumps(sources or [])
            )
            db.add(as_row)
            db.commit()

        return StreamingResponse(stream_generator(), media_type="text/plain")
    else:
        try:
            reply = await ollama_chat(req.model, messages)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

        as_row = models.ChatMessage(
            session_pk=session.id, role='assistant', content=reply,
            sources_json=json.dumps(sources or [])
        )
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
        title = await ollama_chat(req.model, [{"role": "user", "content": prompt}])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

    print(f"Original title from LLM: {title}") # Debugging line to see the raw title

    # HTML unescape the title first to handle encoded tags
    unescaped_title = html.unescape(title)
    print(f"Unescaped title: {unescaped_title}") # Debugging line to see the unescaped title

    # Remove <think> blocks from the unescaped title
    # Use re.IGNORECASE to handle potential variations in casing (e.g., <Think>)
    cleaned_title = re.sub(r'<think>.*?</think>', '', unescaped_title, flags=re.DOTALL | re.IGNORECASE)
    
    print(f"Cleaned title before saving: {cleaned_title.strip()}") # Debugging line to see the cleaned title

    session.name = cleaned_title.strip() # Use .strip() to remove any leading/trailing whitespace after removal
    db.commit()

    return {"title": cleaned_title.strip()}

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

@app.put("/sessions/{session_id}/messages/{index}")
def update_user_message(session_id: str, index: int, req: schemas.EditMessageRequest, db: Session = Depends(get_db)):
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    msgs = (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.session_pk == session.id)
        .order_by(models.ChatMessage.created_at.asc())
        .all()
    )

    if index < 0 or index >= len(msgs):
        raise HTTPException(status_code=404, detail="Message index out of range")

    # Only user messages can be edited per spec
    if msgs[index].role != "user":
        raise HTTPException(status_code=400, detail="Only user messages can be edited")

    # Update the content
    msgs[index].content = req.message

    # Drop everything after the edited message
    for m in msgs[index + 1:]:
        db.delete(m)

    db.commit()
    return {"ok": True}

# ADD or REPLACE this whole function
@app.post("/sessions/{session_id}/regenerate")
async def regenerate(session_id: str, req: schemas.RegenerateRequest, db: Session = Depends(get_db)):
    idx = req.index
    model = req.model
    stream = bool(req.stream)
    sources = req.sources or []

    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    msgs = (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.session_pk == session.id)
        .order_by(models.ChatMessage.created_at.asc())
        .all()
    )
    if idx < 0 or idx >= len(msgs):
        raise HTTPException(status_code=400, detail="Invalid message index")

    # last user idx at/before idx
    last_user_idx = idx
    for i in range(idx, -1, -1):
        if msgs[i].role == "user":
            last_user_idx = i
            break

    # prune after that user
    if last_user_idx < len(msgs) - 1:
        for m in msgs[last_user_idx + 1:]:
            db.delete(m)
        db.commit()

    conversation = [{"role": m.role, "content": m.content} for m in msgs[: last_user_idx + 1]]

    if req.enriched_message:
        for j in range(len(conversation) - 1, -1, -1):
            if conversation[j].get("role") == "user":
                conversation = conversation.copy()
                conversation[j] = {**conversation[j], "content": req.enriched_message}
                break

    session_pk = session.id

    if stream:
        async def stream_generator():
            full_reply = ""
            try:
                async for chunk in ollama_chat_stream(model, conversation):
                    full_reply += chunk
                    yield chunk
            except Exception as e:
                yield f"Ollama error: {e}"
            # persist (with sources)
            try:
                db_sess = SessionLocal()
                db_sess.add(models.ChatMessage(
                    session_pk=session_pk, role="assistant", content=full_reply,
                    sources_json=json.dumps(sources or [])
                ))
                db_sess.commit()
            finally:
                try:
                    db_sess.close()
                except Exception:
                    pass

        return StreamingResponse(stream_generator(), media_type="text/plain")

    try:
        reply = await ollama_chat(model, conversation)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

    db.add(models.ChatMessage(
        session_pk=session_pk, role="assistant", content=reply,
        sources_json=json.dumps(sources or [])
    ))
    db.commit()
    return {"reply": reply}


# -----------------------------------------------------------------------------
# Web search enrichment endpoint
@app.post("/websearch", response_model=schemas.WebSearchResponse)
async def websearch_route(req: schemas.WebSearchRequest):
    """
    Return an enriched prompt (with citations) for a given user prompt.
    Optionally uses the last `history_limit` turns from `req.messages`.
    """
    try:
        messages = (req.messages or [])[-int(req.history_limit or 8):]
        enriched, sources = await enrich_prompt(
            user_prompt=req.prompt,
            model=req.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            searx_url=req.searx_url,
            engines=req.engines,
        )
        context_block = ""
        if "<websearch_context>" in enriched:
            context_block = enriched[enriched.index("<websearch_context>"):].strip()
        return {"enriched_prompt": enriched, "sources": sources, "context_block": context_block}
    except Exception:
        return {"enriched_prompt": req.prompt, "sources": [], "context_block": ""}

# To run standalone: python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
