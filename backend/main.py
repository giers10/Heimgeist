import asyncio
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Any, List, Optional
import re
import html
import json
import base64
import mimetypes
import shutil
import tempfile
from pathlib import Path
from . import models, schemas
from .agent import models as agent_models
from .agent.api import initialize_agent_system, router as agent_router
from .chat_memory import (
    ensure_chat_memory_schema,
    safe_backfill_chat_memory,
    safe_delete_chat_memory_for_session,
    safe_sync_chat_memory_for_session,
)
from .database import Base, engine, SessionLocal, ensure_sources_column
from .local_rag import router as local_rag_router, save_chat_message_snapshot
from .ollama_admin import inspect_ollama_startup, prepare_startup_models, pull_local_model, start_local_ollama
from .ollama_client import (
    list_model_catalog as ollama_list_model_catalog,
    chat as ollama_chat,
    chat_stream as ollama_chat_stream,
    show_model as ollama_show_model,
    supports_vision as ollama_supports_vision,
)
from .whisper_admin import DEFAULT_WHISPER_MODEL, list_whisper_models, transcribe_audio_bytes
from .websearch import enrich_prompt

# Create tables + ensure migration
Base.metadata.create_all(bind=engine)
ensure_sources_column(engine)
ensure_chat_memory_schema(engine)
initialize_agent_system()

app = FastAPI(title="LLM Desktop Backend", version="0.1.0" )


def sanitize_chat_title(title: str) -> str:
    cleaned_title = html.unescape(title or "")
    cleaned_title = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', cleaned_title, flags=re.DOTALL | re.IGNORECASE)
    cleaned_title = cleaned_title.strip()

    previous_title = None
    while cleaned_title and cleaned_title != previous_title:
        previous_title = cleaned_title
        cleaned_title = re.sub(r'^\s*#+\s*', '', cleaned_title)
        cleaned_title = re.sub(r'^\s*\*{1,2}\s*', '', cleaned_title)
        cleaned_title = re.sub(r'\s*\*{1,2}\s*$', '', cleaned_title)
        cleaned_title = cleaned_title.strip()

    cleaned_title = re.sub(r'\s+', ' ', cleaned_title)
    return cleaned_title.strip()

# CORS (dev-friendly; tighten later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(local_rag_router)
app.include_router(agent_router)


@app.on_event("startup")
async def backfill_chat_memory_on_startup():
    asyncio.create_task(asyncio.to_thread(safe_backfill_chat_memory, SessionLocal))

_IMAGE_DATA_URL_RE = re.compile(r"^data:(image\/[a-z0-9.+-]+);base64,([a-z0-9+/=\s]+)$", re.IGNORECASE)
_CHAT_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".avif"}
_CHAT_FILE_EXTENSIONS = {
    ".pdf", ".html", ".htm", ".txt", ".md", ".rst", ".epub",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac",
    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts",
}


def _attachment_field(item: Any, field: str) -> Any:
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)


def _normalize_attachment_name(value: Any, fallback: str) -> str:
    name = str(value or "").strip() or fallback
    return name[:255]


def _normalize_image_attachment(item: Any) -> Optional[dict]:
    data_url = str(_attachment_field(item, "data_url") or "").strip()
    match = _IMAGE_DATA_URL_RE.match(data_url)
    if not match:
        return None

    detected_mime = match.group(1).lower()
    payload = re.sub(r"\s+", "", match.group(2))
    mime_type = str(_attachment_field(item, "mime_type") or "").strip().lower()
    if mime_type and not mime_type.startswith("image/"):
        return None

    try:
        base64.b64decode(payload, validate=True)
    except Exception:
        return None

    return {
        "kind": "image",
        "name": _normalize_attachment_name(_attachment_field(item, "name"), "image"),
        "mime_type": mime_type or detected_mime,
        "data_url": f"data:{detected_mime};base64,{payload}",
    }


def _normalize_file_attachment(item: Any, *, include_text: bool = False) -> Optional[dict]:
    source_path_raw = str(_attachment_field(item, "source_path") or "").strip()
    source_path = ""
    if source_path_raw:
        try:
            source_path = str(Path(source_path_raw).expanduser().resolve())
        except Exception:
            source_path = source_path_raw

    text_value = str(_attachment_field(item, "text") or "")
    name_seed = _attachment_field(item, "name") or (Path(source_path).name if source_path else "file")
    name = _normalize_attachment_name(name_seed, "file")
    mime_type = str(_attachment_field(item, "mime_type") or "").split(";", 1)[0].strip().lower()
    record_type = str(_attachment_field(item, "record_type") or "").strip().lower() or None
    size = _attachment_field(item, "size")
    try:
        size = max(0, int(size)) if size is not None else None
    except Exception:
        size = None

    extension = Path(source_path or name).suffix.lower()
    if source_path and extension not in _CHAT_FILE_EXTENSIONS:
        return None
    if not source_path and not text_value.strip():
        return None

    payload = {
        "kind": "file",
        "name": name,
        "mime_type": mime_type or (mimetypes.guess_type(name)[0] or "").lower() or None,
        "source_path": source_path or None,
        "size": size,
        "record_type": record_type,
    }
    if include_text and text_value.strip():
        payload["text"] = text_value.strip()
    return payload


def _normalize_chat_attachments(items: Any, *, include_text: bool = False) -> List[dict]:
    cleaned: List[dict] = []
    for item in items or []:
        kind = str(_attachment_field(item, "kind") or "").strip().lower()
        data_url = str(_attachment_field(item, "data_url") or "").strip()
        if kind == "image" or (not kind and data_url):
            normalized = _normalize_image_attachment(item)
        else:
            normalized = _normalize_file_attachment(item, include_text=include_text)
        if normalized:
            cleaned.append(normalized)
    return cleaned


def _normalize_chat_attachment_or_raise(item: Any, *, include_text: bool = False) -> dict:
    kind = str(_attachment_field(item, "kind") or "").strip().lower()
    data_url = str(_attachment_field(item, "data_url") or "").strip()
    source_path = str(_attachment_field(item, "source_path") or "").strip()
    name_hint = _attachment_field(item, "name") or (Path(source_path).name if source_path else None) or "attachment"
    label = _normalize_attachment_name(name_hint, "attachment")

    if kind == "image" or (not kind and data_url):
        normalized = _normalize_image_attachment(item)
        if normalized:
            return normalized
        raise HTTPException(status_code=400, detail=f"{label}: invalid image attachment.")

    normalized = _normalize_file_attachment(item, include_text=include_text)
    if normalized:
        return normalized

    if source_path:
        extension = Path(source_path).suffix.lower()
        if extension in _CHAT_IMAGE_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"{label}: use Add Image(s) for image attachments.")
        raise HTTPException(status_code=400, detail=f"{label}: unsupported file type for chat attachment.")

    raise HTTPException(status_code=400, detail=f"{label}: local file path is required for non-image attachments.")


def _load_message_attachments(raw_value: Any, *, include_text: bool = False) -> List[dict]:
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value or "[]")
        except Exception:
            parsed = []
    else:
        parsed = raw_value
    return _normalize_chat_attachments(parsed, include_text=include_text)


def _attachment_is_image(attachment: dict) -> bool:
    kind = str(attachment.get("kind") or "").strip().lower()
    if kind == "image":
        return True
    return bool(_IMAGE_DATA_URL_RE.match(str(attachment.get("data_url") or "").strip()))


def _attachment_is_file(attachment: dict) -> bool:
    return not _attachment_is_image(attachment)


def _attachments_to_ollama_images(attachments: List[dict]) -> List[str]:
    images: List[str] = []
    for attachment in attachments:
        if not _attachment_is_image(attachment):
            continue
        match = _IMAGE_DATA_URL_RE.match(str(attachment.get("data_url") or "").strip())
        if not match:
            continue
        images.append(re.sub(r"\s+", "", match.group(2)))
    return images


def _attachment_history_payload(attachment: dict) -> dict:
    payload = {
        "kind": attachment.get("kind") or ("image" if _attachment_is_image(attachment) else "file"),
        "name": attachment.get("name"),
        "mime_type": attachment.get("mime_type"),
    }
    if _attachment_is_image(attachment):
        payload["data_url"] = attachment.get("data_url")
    else:
        payload["source_path"] = attachment.get("source_path")
        payload["size"] = attachment.get("size")
        payload["record_type"] = attachment.get("record_type")
    return {key: value for key, value in payload.items() if value not in (None, "", [])}


async def _model_supports_vision(model_name: Optional[str]) -> bool:
    normalized = str(model_name or "").strip()
    if not normalized:
        return False
    try:
        model_data = await ollama_show_model(normalized)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama not available: {exc}") from exc
    return ollama_supports_vision(model_data)


def _safe_temp_attachment_name(index: int, name: str, fallback_suffix: str = "") -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or f"attachment-{index + 1}"
    suffix = Path(safe_name).suffix
    if fallback_suffix and not suffix:
        safe_name = f"{safe_name}{fallback_suffix}"
    return f"{index:03d}-{safe_name}"


def _materialize_attachment_source(attachment: dict, stage_root: Path, index: int) -> tuple[Path, dict]:
    if _attachment_is_image(attachment):
        match = _IMAGE_DATA_URL_RE.match(str(attachment.get("data_url") or "").strip())
        if not match:
            raise RuntimeError(f"{attachment.get('name') or 'image'}: invalid image payload.")
        mime_type = match.group(1).lower()
        payload = re.sub(r"\s+", "", match.group(2))
        try:
            image_bytes = base64.b64decode(payload, validate=True)
        except Exception as exc:
            raise RuntimeError(f"{attachment.get('name') or 'image'}: invalid base64 image payload.") from exc
        suffix = Path(str(attachment.get("name") or "")).suffix or mimetypes.guess_extension(mime_type) or ".img"
        target_path = stage_root / _safe_temp_attachment_name(index, str(attachment.get("name") or "image"), suffix)
        target_path.write_bytes(image_bytes)
        return target_path, {
            "source_path": str(target_path.resolve()),
            "size": len(image_bytes),
        }

    source_path = str(attachment.get("source_path") or "").strip()
    if not source_path:
        raise RuntimeError(f"{attachment.get('name') or 'file'}: local file path is required.")

    source = Path(source_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise RuntimeError(f"{attachment.get('name') or source.name}: file is no longer available.")
    if source.suffix.lower() not in _CHAT_FILE_EXTENSIONS:
        raise RuntimeError(f"{source.name}: unsupported file type for chat attachment.")

    target_path = stage_root / _safe_temp_attachment_name(index, source.name)
    try:
        target_path.symlink_to(source)
    except Exception:
        shutil.copy2(source, target_path)
    return target_path, {
        "source_path": str(source),
        "size": int(source.stat().st_size),
    }


def _read_corpus_records(path: Path) -> List[dict]:
    records: List[dict] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _extract_attachment_texts(
    attachments: List[dict],
    *,
    vision_model: Optional[str],
    transcription_model: Optional[str],
) -> List[dict]:
    from .rag.corpus_builder import run_build

    if not attachments:
        return []

    with tempfile.TemporaryDirectory(prefix="heimgeist-chat-attachments-") as tmpdir:
        temp_root = Path(tmpdir)
        stage_root = temp_root / "stage"
        stage_root.mkdir(parents=True, exist_ok=True)
        corpus_path = temp_root / "corpus.jsonl"

        index_by_key: dict[str, int] = {}
        attachment_meta: List[dict] = []
        for index, attachment in enumerate(attachments):
            temp_path, extra_meta = _materialize_attachment_source(attachment, stage_root, index)
            index_by_key[str(temp_path)] = index
            index_by_key[str(temp_path.resolve())] = index
            attachment_meta.append(extra_meta)

        result = run_build(
            root=stage_root,
            out=corpus_path,
            emit="per-file",
            emit_av="joined",
            lang_detect=False,
            whisper_model=transcription_model or DEFAULT_WHISPER_MODEL,
            vlm_model=vision_model or "qwen2.5vl:7b",
        )

        errors = [str(error).strip() for error in (result.get("errors") or []) if str(error).strip()]
        if errors:
            raise RuntimeError("; ".join(errors))

        extracted: dict[int, dict[str, Any]] = {}
        for record in _read_corpus_records(corpus_path):
            record_id = str(record.get("id") or "").split("#", 1)[0].strip()
            source_path = str(record.get("source_path") or "").strip()
            matched_index = None
            for candidate in (record_id, source_path):
                if candidate in index_by_key:
                    matched_index = index_by_key[candidate]
                    break
            if matched_index is None:
                continue

            bucket = extracted.setdefault(matched_index, {
                "mime_type": "",
                "record_type": "",
                "text_parts": [],
            })
            if not bucket["mime_type"]:
                bucket["mime_type"] = str(record.get("mime") or "").strip().lower()
            if not bucket["record_type"]:
                bucket["record_type"] = str(record.get("record_type") or "").strip().lower()

            text = str(record.get("text") or "").strip()
            if text:
                bucket["text_parts"].append(text)

        prepared: List[dict] = []
        for index, attachment in enumerate(attachments):
            bucket = extracted.get(index) or {}
            text = "\n\n".join(
                part for part in bucket.get("text_parts") or []
                if str(part or "").strip()
            ).strip()
            if not text:
                raise RuntimeError(f"{attachment.get('name') or 'attachment'}: no usable text could be extracted.")

            extra_meta = attachment_meta[index]
            prepared.append({
                **attachment,
                "mime_type": bucket.get("mime_type") or attachment.get("mime_type"),
                "record_type": bucket.get("record_type") or attachment.get("record_type"),
                "source_path": extra_meta.get("source_path") or attachment.get("source_path"),
                "size": extra_meta.get("size") if extra_meta.get("size") is not None else attachment.get("size"),
                "text": text,
            })

        return prepared


def _build_attachment_block(attachments: List[dict]) -> str:
    sections: List[str] = []
    for index, attachment in enumerate(attachments, start=1):
        text = str(attachment.get("text") or "").strip()
        if not text:
            continue
        lines = [
            f"[Attachment {index}]",
            f"Name: {attachment.get('name') or f'attachment-{index}'}",
            f"Kind: {'image' if _attachment_is_image(attachment) else 'file'}",
        ]
        mime_type = str(attachment.get("mime_type") or "").strip()
        record_type = str(attachment.get("record_type") or "").strip()
        if mime_type:
            lines.append(f"MIME: {mime_type}")
        if record_type:
            lines.append(f"Record type: {record_type}")
        lines.append("Content:")
        lines.append(text)
        sections.append("\n".join(lines))

    if not sections:
        return ""
    return "<chat_attachments>\n" + "\n\n".join(sections) + "\n</chat_attachments>"


def _compose_user_message_content(message: str, enriched_message: Optional[str], attachment_block: str) -> str:
    base_content = str(enriched_message or "").strip() or str(message or "").strip()
    parts: List[str] = []
    if base_content:
        parts.append(base_content)
    elif attachment_block:
        parts.append("Please analyze the attached material.")
    if attachment_block:
        parts.append(attachment_block)
    if not parts:
        return "Please analyze the attached material."
    return "\n\n".join(part for part in parts if str(part or "").strip()).strip()


def _attachments_have_images(attachments: List[dict]) -> bool:
    return any(_attachment_is_image(attachment) for attachment in attachments or [])


async def _prepare_chat_message_attachments(
    attachments: List[dict],
    *,
    request_model_supports_vision: bool,
    vision_model: Optional[str],
    transcription_model: Optional[str],
    persist_file_text: bool,
) -> tuple[List[dict], List[str], str]:
    prepared = [
        _normalize_chat_attachment_or_raise(item, include_text=True)
        for item in (attachments or [])
    ]
    if not prepared:
        return [], [], ""

    if _attachments_have_images(prepared) and not request_model_supports_vision:
        if not str(vision_model or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Image attachments require a configured vision model when the selected chat model does not support vision.",
            )
        if not await _model_supports_vision(vision_model):
            raise HTTPException(
                status_code=400,
                detail="The configured vision model does not support image inputs.",
            )

    prompt_entries: List[Optional[dict]] = [None] * len(prepared)
    attachments_to_extract: List[dict] = []
    extract_indexes: List[int] = []

    for index, attachment in enumerate(prepared):
        if _attachment_is_image(attachment):
            if request_model_supports_vision:
                continue
            attachments_to_extract.append(attachment)
            extract_indexes.append(index)
            continue

        text = str(attachment.get("text") or "").strip()
        if text:
            prompt_entries[index] = attachment
        else:
            attachments_to_extract.append(attachment)
            extract_indexes.append(index)

    if attachments_to_extract:
        extracted_attachments = await asyncio.to_thread(
            _extract_attachment_texts,
            attachments_to_extract,
            vision_model=vision_model,
            transcription_model=transcription_model,
        )
        for original_index, extracted in zip(extract_indexes, extracted_attachments):
            if _attachment_is_file(prepared[original_index]) and persist_file_text:
                prepared[original_index] = {
                    **prepared[original_index],
                    "mime_type": extracted.get("mime_type") or prepared[original_index].get("mime_type"),
                    "record_type": extracted.get("record_type") or prepared[original_index].get("record_type"),
                    "source_path": extracted.get("source_path") or prepared[original_index].get("source_path"),
                    "size": extracted.get("size") if extracted.get("size") is not None else prepared[original_index].get("size"),
                    "text": extracted.get("text") or prepared[original_index].get("text"),
                }
                prompt_entries[original_index] = prepared[original_index]
            else:
                prompt_entries[original_index] = extracted

    prompt_attachments = [entry for entry in prompt_entries if entry and str(entry.get("text") or "").strip()]
    ollama_images = _attachments_to_ollama_images(prepared) if request_model_supports_vision else []
    return prepared, ollama_images, _build_attachment_block(prompt_attachments)


def _row_to_history_message(row: models.ChatMessage) -> dict:
    sources = []
    try:
        if getattr(row, "sources_json", None):
            sources = json.loads(row.sources_json or "[]")
    except Exception:
        sources = []

    attachments = _load_message_attachments(getattr(row, "attachments_json", None))
    payload = {
        "message_id": row.message_id,
        "role": row.role,
        "content": row.content,
        "sources": sources,
        "workflow_id": getattr(row, "workflow_id", None),
        "workflow_revision_id": getattr(row, "workflow_revision_id", None),
        "workflow_run_id": getattr(row, "workflow_run_id", None),
    }
    for field_name, payload_name in (("agent_summary_json", "agent_summary"), ("usage_json", "usage")):
        try:
            payload[payload_name] = json.loads(getattr(row, field_name, None) or "{}")
        except Exception:
            payload[payload_name] = {}
    if attachments:
        payload["attachments"] = [_attachment_history_payload(attachment) for attachment in attachments]
    return payload


async def _build_ollama_user_message(
    message: str,
    attachments: List[dict],
    *,
    enriched_message: Optional[str],
    request_model_supports_vision: bool,
    vision_model: Optional[str],
    transcription_model: Optional[str],
    persist_file_text: bool,
) -> tuple[dict, List[dict]]:
    prepared_attachments, ollama_images, attachment_block = await _prepare_chat_message_attachments(
        attachments,
        request_model_supports_vision=request_model_supports_vision,
        vision_model=vision_model,
        transcription_model=transcription_model,
        persist_file_text=persist_file_text,
    )

    payload = {
        "role": "user",
        "content": _compose_user_message_content(message, enriched_message, attachment_block),
    }
    if ollama_images:
        payload["images"] = ollama_images
    return payload, prepared_attachments


async def _build_ollama_messages_from_rows(
    rows: List[models.ChatMessage],
    *,
    request_model_supports_vision: bool,
    vision_model: Optional[str],
    transcription_model: Optional[str],
    override_user_row_index: Optional[int] = None,
    override_user_content: Optional[str] = None,
) -> List[dict]:
    conversation: List[dict] = []
    for row_index, row in enumerate(rows):
        if row.role != "user":
            conversation.append({"role": row.role, "content": row.content})
            continue

        attachments = _load_message_attachments(getattr(row, "attachments_json", None), include_text=True)
        enriched_message = override_user_content if override_user_row_index == row_index else None
        message_payload, _prepared = await _build_ollama_user_message(
            row.content,
            attachments,
            enriched_message=enriched_message,
            request_model_supports_vision=request_model_supports_vision,
            vision_model=vision_model,
            transcription_model=transcription_model,
            persist_file_text=False,
        )
        conversation.append(message_payload)

    return conversation

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/audio/transcribe", response_model=schemas.AudioTranscriptionResponse)
async def transcribe_audio_route(req: schemas.AudioTranscriptionRequest):
    mime_type = str(req.mime_type or "").split(";", 1)[0].strip().lower()
    if not mime_type.startswith("audio/"):
        raise HTTPException(status_code=400, detail="An audio mime type is required.")

    payload = re.sub(r"\s+", "", str(req.audio_base64 or ""))
    if not payload:
        raise HTTPException(status_code=400, detail="Audio payload is required.")

    try:
        audio_bytes = base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 audio payload.") from exc

    try:
        result = await asyncio.to_thread(
            transcribe_audio_bytes,
            audio_bytes,
            mime_type,
            req.model or DEFAULT_WHISPER_MODEL,
            req.language,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Audio transcription failed: {exc}") from exc

    return {
        "text": str(result.get("text") or "").strip(),
        "language": str(result.get("language") or "").strip() or None,
        "model": str(result.get("model") or req.model or DEFAULT_WHISPER_MODEL),
    }

@app.get("/models")
async def get_models():
    try:
        ollama_data, whisper_data = await asyncio.gather(
            ollama_list_model_catalog(),
            asyncio.to_thread(list_whisper_models),
        )
        return {
            **ollama_data,
            "whisper_models": whisper_data.get("models", []),
            "whisper_error": whisper_data.get("error", ""),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama not available: {e}")


@app.get("/models/capabilities")
async def get_model_capabilities(name: str):
    model_name = str(name or "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="Model name is required.")

    try:
        model_data = await ollama_show_model(model_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama not available: {e}")

    capabilities = [
        str(item).strip()
        for item in (model_data.get("capabilities") or [])
        if str(item).strip()
    ]
    return {
        "name": model_name,
        "capabilities": capabilities,
        "supports_vision": ollama_supports_vision(model_data),
    }


@app.get("/ollama/startup-status")
async def ollama_startup_status():
    return await inspect_ollama_startup()


@app.post("/ollama/start")
async def ollama_start_route():
    try:
        return await start_local_ollama()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/ollama/pull")
async def ollama_pull_route(req: schemas.OllamaPullRequest):
    try:
        return await pull_local_model(req.model)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/startup/prepare-models")
async def startup_prepare_models_route():
    try:
        return await prepare_startup_models()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/sessions", response_model=schemas.SessionsResponse)
def get_sessions(db: Session = Depends(get_db)):
    sessions = db.query(models.ChatSession).order_by(models.ChatSession.created_at.desc()).all()
    return {
        "sessions": [
            {
                "id": session.id,
                "session_id": session.session_id,
                "name": sanitize_chat_title(session.name),
                "created_at": session.created_at,
            }
            for session in sessions
        ]
    }

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
    msgs = [_row_to_history_message(r) for r in rows]
    return {"messages": msgs}


def _default_knowledge_title(content: str, role: str) -> str:
    clean = re.sub(r"\s+", " ", str(content or "")).strip()
    if len(clean) > 90:
        clean = clean[:87].rstrip() + "..."
    prefix = "User note" if role == "user" else "Saved answer"
    return f"{prefix}: {clean}" if clean else prefix


@app.post("/libraries/{slug}/chat-messages")
async def save_message_to_knowledge(
    slug: str,
    req: schemas.SaveMessageToKnowledgeRequest,
    db: Session = Depends(get_db),
):
    return await _save_message_to_knowledge_impl(
        slug,
        req.message_id,
        db,
        title=req.title,
        content=req.content,
        saved_by="user",
    )


async def _save_message_to_knowledge_impl(
    slug: str,
    message_id: str,
    db: Session,
    *,
    title: Optional[str] = None,
    content: Optional[str] = None,
    saved_by: str = "user",
):
    row = (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.message_id == str(message_id or "").strip())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Chat message not found.")

    session = db.query(models.ChatSession).filter(models.ChatSession.id == row.session_pk).first()
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")

    sources = []
    try:
        sources = json.loads(row.sources_json or "[]")
    except Exception:
        sources = []

    previous_user = None
    if row.role == "assistant":
        previous_user = (
            db.query(models.ChatMessage)
            .filter(
                models.ChatMessage.session_pk == row.session_pk,
                models.ChatMessage.role == "user",
                models.ChatMessage.id < row.id,
            )
            .order_by(models.ChatMessage.id.desc())
            .first()
        )

    default_content = row.content
    if row.role == "assistant" and previous_user:
        default_content = (
            f"User question:\n{previous_user.content.strip()}\n\n"
            f"Assistant response:\n{row.content.strip()}"
        )

    content = str(content if content is not None else default_content).strip()
    title = str(title or _default_knowledge_title(row.content, row.role)).strip()
    return await save_chat_message_snapshot(
        slug,
        title=title,
        content=content,
        source_message_id=row.message_id,
        source_session_id=session.session_id,
        source_session_title=sanitize_chat_title(session.name),
        source_role=row.role,
        source_created_at=row.created_at.isoformat() if row.created_at else None,
        sources=[str(source) for source in sources if str(source).strip()],
        saved_by=saved_by,
    )

@app.post("/chat")
async def chat(req: schemas.ChatRequest, db: Session = Depends(get_db)):
    # Find or create session
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == req.session_id).first()
    if not session:
        session = models.ChatSession(session_id=req.session_id)
        db.add(session)
        db.commit()
        db.refresh(session)

    request_model_supports_vision = await _model_supports_vision(req.model)
    prior_rows = (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.session_pk == session.id)
        .order_by(models.ChatMessage.created_at.asc())
        .all()
    )
    history_rows = prior_rows[-19:]
    history_messages = await _build_ollama_messages_from_rows(
        history_rows,
        request_model_supports_vision=request_model_supports_vision,
        vision_model=req.vision_model,
        transcription_model=req.transcription_model,
    )
    memory_context = _chat_memory_context(db, req.message, req.session_id)
    enriched_message = _merge_enriched_context(
        req.message,
        req.enriched_message,
        [memory_context.get("context_block")],
    )

    current_user_message, user_attachments = await _build_ollama_user_message(
        req.message,
        req.attachments or [],
        enriched_message=enriched_message,
        request_model_supports_vision=request_model_supports_vision,
        vision_model=req.vision_model,
        transcription_model=req.transcription_model,
        persist_file_text=True,
    )

    session_pk = session.id

    user_row = models.ChatMessage(
        session_pk=session_pk,
        role='user',
        content=req.message,
        attachments_json=json.dumps(user_attachments or []),
    )
    db.add(user_row)
    db.commit()
    messages = [*history_messages, current_user_message]

    # Sources to persist with the assistant reply
    sources = _dedupe_sources([*(req.sources or []), *(memory_context.get("sources") or [])])

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
            db_sess = None
            try:
                db_sess = SessionLocal()
                db_sess.add(models.ChatMessage(
                    session_pk=session_pk,
                    role='assistant',
                    content=full_reply,
                    sources_json=json.dumps(sources or []),
                ))
                db_sess.commit()
                safe_sync_chat_memory_for_session(db_sess, req.session_id)
            finally:
                if db_sess is not None:
                    db_sess.close()

        return StreamingResponse(stream_generator(), media_type="text/plain")
    else:
        try:
            reply = await ollama_chat(req.model, messages)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

        as_row = models.ChatMessage(
            session_pk=session_pk, role='assistant', content=reply,
            sources_json=json.dumps(sources or [])
        )
        db.add(as_row)
        db.commit()
        safe_sync_chat_memory_for_session(db, req.session_id)
        return {"reply": reply}

@app.post("/generate-title", response_model=schemas.GenerateTitleResponse)
async def generate_title(req: schemas.GenerateTitleRequest, db: Session = Depends(get_db)):
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == req.session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    prompt = (
        "Generate a short chat title from the concrete subject of the user's first message.\n"
        "Return only the title text, 2 to 5 words.\n"
        "Do not use quotation marks. Do not use meta words such as chat, conversation, query, inquiry, request, or question.\n\n"
        f"First user message: {req.message}"
    )
    
    try:
        title = await ollama_chat(req.model, [{"role": "user", "content": prompt}])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

    print(f"Original title from LLM: {title}") # Debugging line to see the raw title

    cleaned_title = sanitize_chat_title(title)

    print(f"Cleaned title before saving: {cleaned_title}") # Debugging line to see the cleaned title

    session.name = cleaned_title
    db.commit()
    safe_sync_chat_memory_for_session(db, req.session_id)

    return {"title": cleaned_title}

@app.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)):
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    safe_delete_chat_memory_for_session(db, session_id)

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

    session.name = sanitize_chat_title(req.title)
    db.commit()
    safe_sync_chat_memory_for_session(db, session_id)
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
    safe_sync_chat_memory_for_session(db, session_id)
    return {"ok": True}

# ADD or REPLACE this whole function
@app.post("/sessions/{session_id}/regenerate")
async def regenerate(session_id: str, req: schemas.RegenerateRequest, db: Session = Depends(get_db)):
    idx = req.index
    model = req.model
    stream = bool(req.stream)

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

    request_model_supports_vision = await _model_supports_vision(model)
    memory_context = _chat_memory_context(db, msgs[last_user_idx].content, session_id)
    enriched_message = _merge_enriched_context(
        msgs[last_user_idx].content,
        req.enriched_message,
        [memory_context.get("context_block")],
    )
    conversation = await _build_ollama_messages_from_rows(
        msgs[: last_user_idx + 1],
        request_model_supports_vision=request_model_supports_vision,
        vision_model=req.vision_model,
        transcription_model=req.transcription_model,
        override_user_row_index=last_user_idx,
        override_user_content=enriched_message,
    )
    sources = _dedupe_sources([*(req.sources or []), *(memory_context.get("sources") or [])])

    # prune after that user only after conversation building succeeds
    if last_user_idx < len(msgs) - 1:
        for m in msgs[last_user_idx + 1:]:
            db.delete(m)
        db.commit()
        safe_sync_chat_memory_for_session(db, session_id)

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
                safe_sync_chat_memory_for_session(db_sess, session_id)
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
    safe_sync_chat_memory_for_session(db, session_id)
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
