from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import models


DEFAULT_MEMORY_TOP_K = 4
DEFAULT_MEMORY_CONTEXT_CHARS = 3600
_TOKEN_RE = re.compile(r"[\w][\w'_-]*", re.UNICODE)
_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.IGNORECASE | re.DOTALL)
_STOPWORDS = {
    "about", "again", "also", "and", "are", "but", "can", "could", "did", "does", "for",
    "from", "had", "has", "have", "how", "into", "just", "like", "more", "not", "now",
    "our", "out", "over", "please", "should", "that", "the", "then", "there", "this",
    "was", "were", "what", "when", "where", "which", "with", "would", "you", "your",
    "all", "anything", "chat", "chats", "conversation", "conversations", "discuss",
    "discussed", "everything", "list", "me", "talk", "talked", "tell", "topic", "topics",
    "aber", "als", "auch", "auf", "aus", "bei", "bin", "bis", "das", "dem", "den",
    "der", "des", "die", "ein", "eine", "einem", "einen", "einer", "erzaehl", "erzähl",
    "erzähle", "für", "ging", "hat", "ich", "ist", "letzte", "letzten", "letztes", "mir",
    "mit", "nicht", "oder", "sich", "sind", "und", "uns", "über", "ueber", "von",
    "war", "was", "wie", "wir", "worum", "zu", "zum", "zur",
}
_LAST_CONVERSATION_RE = re.compile(
    r"\b(last|latest|previous|prior)\s+(chat|conversation|session)\b"
    r"|\b(letzte[sn]?|vorherige[sn]?)\s+(gespr[aä]ch|chat|unterhaltung)\b"
    r"|\bworum\s+ging\s+es\s+im\s+letzte[sn]?\s+gespr[aä]ch\b",
    re.IGNORECASE,
)
_QUERY_EXPANSIONS = {
    "music": ["song", "songs", "album", "albums", "artist", "artists", "band", "bands", "lied", "lieder", "musik"],
    "musik": ["music", "song", "songs", "album", "albums", "artist", "artists", "band", "bands", "lied", "lieder"],
    "song": ["music", "songs", "lied", "lieder", "musik"],
    "songs": ["music", "song", "lied", "lieder", "musik"],
    "lied": ["music", "musik", "song", "songs", "lieder"],
    "lieder": ["music", "musik", "song", "songs", "lied"],
}


def ensure_chat_memory_schema(engine) -> None:
    """Create the lightweight chat memory tables used for past-chat retrieval."""
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS chat_memory_turns (
                turn_key TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                session_name TEXT NOT NULL DEFAULT '',
                user_message_id TEXT NOT NULL,
                assistant_message_id TEXT NOT NULL,
                user_message_row_id INTEGER NOT NULL,
                assistant_message_row_id INTEGER NOT NULL,
                user_content TEXT NOT NULL,
                assistant_content TEXT NOT NULL,
                attachment_summary TEXT NOT NULL DEFAULT '',
                search_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_chat_memory_turns_session_id "
            "ON chat_memory_turns (session_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_chat_memory_turns_assistant_row "
            "ON chat_memory_turns (assistant_message_row_id)"
        ))
        try:
            conn.execute(text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chat_memory_turns_fts USING fts5(
                    turn_key UNINDEXED,
                    session_id UNINDEXED,
                    session_name,
                    user_content,
                    assistant_content,
                    attachment_summary,
                    search_text,
                    tokenize = 'unicode61'
                )
                """
            ))
        except Exception as exc:
            print("[chat-memory] FTS index unavailable:", exc)


def _ensure_schema_for_session(db: Session) -> None:
    try:
        ensure_chat_memory_schema(db.get_bind())
    except Exception:
        pass


def _clean_content(value: Any, limit: int = 12_000) -> str:
    text_value = _THINK_RE.sub("", str(value or ""))
    text_value = re.sub(r"\s+", " ", text_value).strip()
    if len(text_value) > limit:
        return text_value[:limit].rstrip() + "..."
    return text_value


def _attachment_summary(raw_value: Any) -> str:
    try:
        items = json.loads(raw_value or "[]") if isinstance(raw_value, str) else (raw_value or [])
    except Exception:
        return ""
    labels: List[str] = []
    iterable = items if isinstance(items, list) else []
    for item in iterable:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("source_path") or "").strip()
        if name:
            labels.append(name[:160])
    return ", ".join(labels[:8])


def _completed_turns(session: models.ChatSession, rows: Sequence[models.ChatMessage]) -> List[Dict[str, Any]]:
    turns: List[Dict[str, Any]] = []
    pending_user: Optional[models.ChatMessage] = None
    session_title = _clean_content(session.name, limit=200) or "New Chat"
    for row in rows:
        if row.role == "user":
            pending_user = row
            continue
        if row.role != "assistant" or pending_user is None:
            continue

        user_content = _clean_content(pending_user.content)
        assistant_content = _clean_content(row.content)
        if not user_content or not assistant_content:
            pending_user = None
            continue

        attachments = _attachment_summary(getattr(pending_user, "attachments_json", None))
        created_at = row.created_at.isoformat() if row.created_at else datetime.utcnow().isoformat()
        turn_key = f"{pending_user.message_id}:{row.message_id}"
        search_text = "\n".join(part for part in (
            session_title,
            attachments,
            f"User: {user_content}",
            f"Assistant: {assistant_content}",
        ) if part.strip())
        turns.append({
            "turn_key": turn_key,
            "session_id": session.session_id,
            "session_name": session_title,
            "user_message_id": pending_user.message_id,
            "assistant_message_id": row.message_id,
            "user_message_row_id": pending_user.id,
            "assistant_message_row_id": row.id,
            "user_content": user_content,
            "assistant_content": assistant_content,
            "attachment_summary": attachments,
            "search_text": search_text,
            "created_at": created_at,
            "updated_at": datetime.utcnow().isoformat(),
        })
        pending_user = None
    return turns


def _delete_fts_rows(db: Session, keys: Sequence[str]) -> None:
    if not keys:
        return
    try:
        for turn_key in keys:
            db.execute(text("DELETE FROM chat_memory_turns_fts WHERE turn_key = :turn_key"), {"turn_key": turn_key})
    except Exception:
        pass


def _insert_turn(db: Session, turn: Dict[str, Any]) -> None:
    db.execute(text(
        """
        INSERT OR REPLACE INTO chat_memory_turns (
            turn_key, session_id, session_name, user_message_id, assistant_message_id,
            user_message_row_id, assistant_message_row_id, user_content, assistant_content,
            attachment_summary, search_text, created_at, updated_at
        ) VALUES (
            :turn_key, :session_id, :session_name, :user_message_id, :assistant_message_id,
            :user_message_row_id, :assistant_message_row_id, :user_content, :assistant_content,
            :attachment_summary, :search_text, :created_at, :updated_at
        )
        """
    ), turn)
    _delete_fts_rows(db, [turn["turn_key"]])
    try:
        db.execute(text(
            """
            INSERT INTO chat_memory_turns_fts (
                turn_key, session_id, session_name, user_content, assistant_content,
                attachment_summary, search_text
            ) VALUES (
                :turn_key, :session_id, :session_name, :user_content, :assistant_content,
                :attachment_summary, :search_text
            )
            """
        ), turn)
    except Exception:
        pass


def delete_chat_memory_for_session(db: Session, session_id: str, *, commit: bool = True) -> None:
    _ensure_schema_for_session(db)
    keys = [
        row[0]
        for row in db.execute(
            text("SELECT turn_key FROM chat_memory_turns WHERE session_id = :session_id"),
            {"session_id": session_id},
        ).fetchall()
    ]
    _delete_fts_rows(db, keys)
    db.execute(text("DELETE FROM chat_memory_turns WHERE session_id = :session_id"), {"session_id": session_id})
    if commit:
        db.commit()


def sync_chat_memory_for_session(db: Session, session_id: str, *, commit: bool = True) -> int:
    _ensure_schema_for_session(db)
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == session_id).first()
    if session is None:
        delete_chat_memory_for_session(db, session_id, commit=commit)
        return 0

    rows = (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.session_pk == session.id)
        .order_by(models.ChatMessage.created_at.asc(), models.ChatMessage.id.asc())
        .all()
    )
    turns = _completed_turns(session, rows)
    delete_chat_memory_for_session(db, session_id, commit=False)
    for turn in turns:
        _insert_turn(db, turn)
    if commit:
        db.commit()
    return len(turns)


def sync_all_chat_memory(db: Session, *, commit: bool = True) -> int:
    _ensure_schema_for_session(db)
    try:
        db.execute(text("DELETE FROM chat_memory_turns_fts"))
    except Exception:
        pass
    db.execute(text("DELETE FROM chat_memory_turns"))
    count = 0
    sessions = db.query(models.ChatSession).order_by(models.ChatSession.id.asc()).all()
    for session in sessions:
        rows = (
            db.query(models.ChatMessage)
            .filter(models.ChatMessage.session_pk == session.id)
            .order_by(models.ChatMessage.created_at.asc(), models.ChatMessage.id.asc())
            .all()
        )
        for turn in _completed_turns(session, rows):
            _insert_turn(db, turn)
            count += 1
    if commit:
        db.commit()
    return count


def safe_backfill_chat_memory(db_factory) -> None:
    db = db_factory()
    try:
        count = sync_all_chat_memory(db)
        print(f"[chat-memory] indexed {count} completed chat turns")
    except Exception as exc:
        db.rollback()
        print("[chat-memory] backfill failed:", exc)
    finally:
        db.close()


def safe_sync_chat_memory_for_session(db: Session, session_id: Optional[str]) -> None:
    if not session_id:
        return
    try:
        sync_chat_memory_for_session(db, str(session_id))
    except Exception as exc:
        db.rollback()
        print("[chat-memory] session sync failed:", exc)


def safe_delete_chat_memory_for_session(db: Session, session_id: Optional[str]) -> None:
    if not session_id:
        return
    try:
        delete_chat_memory_for_session(db, str(session_id), commit=False)
    except Exception as exc:
        print("[chat-memory] session delete failed:", exc)


def _query_tokens(prompt: str, limit: int = 16) -> List[str]:
    tokens: List[str] = []
    seen = set()
    for raw in _TOKEN_RE.findall(str(prompt or "").casefold()):
        token = raw.strip("'_-")
        if len(token) < 3 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= limit:
            break
    return tokens


def _expanded_query_tokens(tokens: Sequence[str], limit: int = 24) -> List[str]:
    expanded: List[str] = []
    seen = set()
    for token in tokens:
        values = [token, *_QUERY_EXPANSIONS.get(token, [])]
        for value in values:
            cleaned = str(value or "").casefold().strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            expanded.append(cleaned)
            if len(expanded) >= limit:
                return expanded
    return expanded


def _fts_match_query(tokens: Sequence[str]) -> str:
    safe_terms = []
    for token in tokens:
        cleaned = re.sub(r"[^0-9A-Za-z_\u0080-\uffff]", "", token)
        if cleaned:
            safe_terms.append(f'"{cleaned}"')
    return " OR ".join(safe_terms)


def _minimum_memory_score(token_count: int) -> float:
    if token_count <= 1:
        return 0.85
    if token_count == 2:
        return 0.9
    return 0.65


def _row_mapping(row: Any) -> Dict[str, Any]:
    if isinstance(row, dict):
        return row
    try:
        return dict(row._mapping)
    except Exception:
        return dict(row)


def _score_hit(
    row: Dict[str, Any],
    tokens: Sequence[str],
    fts_rank: Optional[float] = None,
    *,
    query_token_count: Optional[int] = None,
) -> float:
    title = str(row.get("session_name") or "").casefold()
    user_text = str(row.get("user_content") or "").casefold()
    assistant_text = str(row.get("assistant_content") or "").casefold()
    attachment_text = str(row.get("attachment_summary") or "").casefold()
    combined = f"{title} {attachment_text} {user_text} {assistant_text}"
    hits = [token for token in tokens if token in combined]
    if not hits:
        return 0.0
    core_count = max(1, int(query_token_count or len(tokens) or 1))
    if core_count >= 2 and len(hits) < 2:
        return 0.0
    score = len(hits) / max(1, min(core_count, 4))
    score += 0.18 * sum(1 for token in hits if token in title)
    score += 0.10 * sum(1 for token in hits if token in user_text)
    if fts_rank is not None:
        try:
            score += min(0.08, 1.0 / (1.0 + abs(float(fts_rank))))
        except Exception:
            pass
    try:
        assistant_row = int(row.get("assistant_message_row_id") or 0)
        score += min(0.04, assistant_row / 2_000_000)
    except Exception:
        pass
    return score


def _load_fts_candidates(
    db: Session,
    tokens: Sequence[str],
    *,
    exclude_session_id: Optional[str],
    limit: int,
    query_token_count: int,
) -> List[Dict[str, Any]]:
    match = _fts_match_query(tokens)
    if not match:
        return []
    try:
        rows = db.execute(text(
            """
            SELECT
                t.turn_key, t.session_id, t.session_name, t.user_message_id,
                t.assistant_message_id, t.user_message_row_id, t.assistant_message_row_id,
                t.user_content, t.assistant_content, t.attachment_summary, t.search_text,
                t.created_at, bm25(chat_memory_turns_fts) AS fts_rank
            FROM chat_memory_turns_fts
            JOIN chat_memory_turns t ON t.turn_key = chat_memory_turns_fts.turn_key
            WHERE chat_memory_turns_fts MATCH :match
              AND (:exclude_session_id IS NULL OR t.session_id != :exclude_session_id)
            ORDER BY fts_rank ASC, t.assistant_message_row_id DESC
            LIMIT :limit
            """
        ), {
            "match": match,
            "exclude_session_id": exclude_session_id,
            "limit": max(limit, 1),
        }).mappings().all()
    except Exception:
        return []
    candidates: List[Dict[str, Any]] = []
    for row in rows:
        item = _row_mapping(row)
        item["_score"] = _score_hit(item, tokens, item.get("fts_rank"), query_token_count=query_token_count)
        if item["_score"] > 0:
            candidates.append(item)
    return candidates


def _load_table_candidates(
    db: Session,
    tokens: Sequence[str],
    *,
    exclude_session_id: Optional[str],
    limit: int,
    query_token_count: int,
) -> List[Dict[str, Any]]:
    try:
        rows = db.execute(text(
            """
            SELECT
                turn_key, session_id, session_name, user_message_id, assistant_message_id,
                user_message_row_id, assistant_message_row_id, user_content, assistant_content,
                attachment_summary, search_text, created_at
            FROM chat_memory_turns
            WHERE (:exclude_session_id IS NULL OR session_id != :exclude_session_id)
            ORDER BY assistant_message_row_id DESC
            LIMIT :limit
            """
        ), {
            "exclude_session_id": exclude_session_id,
            "limit": max(limit, 1),
        }).mappings().all()
    except Exception:
        return []
    candidates: List[Dict[str, Any]] = []
    for row in rows:
        item = _row_mapping(row)
        item["_score"] = _score_hit(item, tokens, query_token_count=query_token_count)
        if item["_score"] > 0:
            candidates.append(item)
    return candidates


def _load_live_candidates(
    db: Session,
    tokens: Sequence[str],
    *,
    exclude_session_id: Optional[str],
    limit: int,
    query_token_count: int,
) -> List[Dict[str, Any]]:
    try:
        rows = db.execute(text(
            """
            SELECT
                s.session_id, s.name AS session_name,
                m.id AS message_row_id, m.message_id, m.role, m.content,
                m.attachments_json, m.created_at
            FROM chat_messages m
            JOIN chat_sessions s ON s.id = m.session_pk
            WHERE (:exclude_session_id IS NULL OR s.session_id != :exclude_session_id)
            ORDER BY s.id ASC, m.created_at ASC, m.id ASC
            LIMIT :limit
            """
        ), {
            "exclude_session_id": exclude_session_id,
            "limit": max(limit, 1),
        }).mappings().all()
    except Exception:
        return []

    turns: List[Dict[str, Any]] = []
    pending_by_session: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        item = _row_mapping(row)
        session_id = str(item.get("session_id") or "")
        if item.get("role") == "user":
            pending_by_session[session_id] = item
            continue
        if item.get("role") != "assistant" or session_id not in pending_by_session:
            continue
        user = pending_by_session.pop(session_id)
        user_content = _clean_content(user.get("content"))
        assistant_content = _clean_content(item.get("content"))
        if not user_content or not assistant_content:
            continue
        attachments = _attachment_summary(user.get("attachments_json"))
        turn = {
            "turn_key": f"{user.get('message_id')}:{item.get('message_id')}",
            "session_id": session_id,
            "session_name": _clean_content(item.get("session_name"), limit=200) or "New Chat",
            "user_message_id": user.get("message_id"),
            "assistant_message_id": item.get("message_id"),
            "user_message_row_id": user.get("message_row_id"),
            "assistant_message_row_id": item.get("message_row_id"),
            "user_content": user_content,
            "assistant_content": assistant_content,
            "attachment_summary": attachments,
            "created_at": str(item.get("created_at") or ""),
        }
        turn["_score"] = _score_hit(turn, tokens, query_token_count=query_token_count)
        if turn["_score"] > 0:
            turns.append(turn)
    return turns


def _trim(value: Any, limit: int) -> str:
    text_value = _clean_content(value, limit=max(limit * 2, limit))
    if len(text_value) <= limit:
        return text_value
    return text_value[:limit].rstrip() + "..."


def _dedupe_hits(candidates: Sequence[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    seen = set()
    ordered = sorted(candidates, key=lambda item: (float(item.get("_score") or 0), int(item.get("assistant_message_row_id") or 0)), reverse=True)
    hits: List[Dict[str, Any]] = []
    for item in ordered:
        key = item.get("turn_key") or (item.get("session_id"), item.get("assistant_message_id"))
        if key in seen:
            continue
        seen.add(key)
        hits.append(item)
        if len(hits) >= top_k:
            break
    return hits


def build_chat_memory_context(
    db: Session,
    prompt: str,
    *,
    exclude_session_id: Optional[str] = None,
    top_k: int = DEFAULT_MEMORY_TOP_K,
    context_character_budget: int = DEFAULT_MEMORY_CONTEXT_CHARS,
) -> Dict[str, Any]:
    tokens = _query_tokens(prompt)
    if not tokens:
        return {"context_block": "", "sources": [], "hits": []}

    candidate_limit = max(80, top_k * 24)
    candidates = _load_fts_candidates(db, tokens, exclude_session_id=exclude_session_id, limit=candidate_limit)
    if len(candidates) < top_k:
        candidates.extend(_load_table_candidates(db, tokens, exclude_session_id=exclude_session_id, limit=max(300, candidate_limit)))
    if len(candidates) < top_k:
        candidates.extend(_load_live_candidates(db, tokens, exclude_session_id=exclude_session_id, limit=1500))

    hits = _dedupe_hits(candidates, max(1, top_k))
    if not hits:
        return {"context_block": "", "sources": [], "hits": []}

    header = (
        "<chat_memory_context>\n"
        "Relevant excerpts from previous chats. Use these only when they help answer the current request; "
        "they may be outdated and should not override explicit current context or fresh retrieved evidence."
    )
    blocks = [header]
    sources: List[Dict[str, Any]] = []
    remaining = max(600, int(context_character_budget)) - len(header) - len("</chat_memory_context>")
    for idx, hit in enumerate(hits, start=1):
        title = _trim(hit.get("session_name") or "Previous chat", 120)
        date = str(hit.get("created_at") or "")[:10]
        user = _trim(hit.get("user_content"), 520)
        assistant = _trim(hit.get("assistant_content"), 900)
        attachments = _trim(hit.get("attachment_summary"), 180)
        attachment_line = f"\nAttachments: {attachments}" if attachments else ""
        chunk = (
            f"[M{idx}] {title}{f' ({date})' if date else ''}{attachment_line}\n"
            f"User: {user}\n"
            f"Assistant: {assistant}"
        )
        if len(chunk) > remaining:
            if remaining < 500:
                break
            chunk = chunk[:remaining].rstrip() + "..."
        blocks.append(chunk)
        remaining -= len(chunk) + 2
        sources.append({
            "type": "chat_memory",
            "title": f"Chat memory: {title}",
            "source_session_id": hit.get("session_id"),
            "source_message_id": hit.get("assistant_message_id"),
            "snippet": assistant[:500],
            "score": round(float(hit.get("_score") or 0), 4),
            "created_at": hit.get("created_at"),
        })
        if remaining <= 0:
            break
    blocks.append("</chat_memory_context>")
    return {"context_block": "\n\n".join(blocks), "sources": sources, "hits": hits[:len(sources)]}
