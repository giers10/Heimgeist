# Heimgeist

Heimgeist is a local desktop chat client for Ollama. It combines a Tauri + React renderer with a FastAPI backend, stores chat history in SQLite, supports optional SearXNG-backed web search, and can enrich prompts with context from local library indexes.

## Features

- Local desktop chat UI with Tauri
- Ollama-backed chat with streaming and non-streaming replies
- Persistent chat sessions and automatic title generation
- Edit-and-regenerate flow for earlier user messages
- Optional web search enrichment with source chips
- Local library management for RAG-style prompt enrichment
- Theme selection and UI scale controls

## Local Libraries

The `DBs` tab is no longer a placeholder. You can:

- create and rename libraries
- register files
- let Heimgeist rebuild retrieval automatically when files change
- open or remove registered files from the UI

When files are added or removed, Heimgeist automatically rebuilds the local RAG pipeline for that database: corpus, enrichment, embeddings, and indexes. In the chat composer, you can select which database the current chat should use. For each chat turn, Heimgeist queries the selected database, turns the top results into a local context block, appends that block to the user prompt, and sends the enriched prompt to Ollama.

## Local Data

Heimgeist stores chat history and local library indexes on the local machine. During development, the backend keeps using `backend/app.db` and `backend/libraries` so existing local data remains available.

Packaged Tauri builds launch the bundled backend with app-managed data paths so chats and local libraries live under the operating system's normal application data location, such as Application Support on macOS, LocalAppData on Windows, or the XDG data directory on Linux. These paths are managed by the app and are not exposed as normal user settings.

## Stack

- Frontend: Tauri, React, Vite
- Backend: FastAPI, SQLAlchemy, SQLite
- Search enrichment: SearXNG + page fetching/reranking
- Local RAG pipeline: corpus build, enrichment, embedding, and retrieval helpers under `backend/rag/`

## Development

Requirements:

- Node.js 18+
- Python 3.13
- Ollama running locally
- Optional: SearXNG on `http://127.0.0.1:8888`

Quick start:

```bash
./run.sh
```

This creates or refreshes `backend/.venv`, installs Python dependencies, installs npm dependencies, and starts the dev stack.

On Linux `x86_64`, `run.sh` now selects a PyTorch flavor before installing `openai-whisper`:

- Steam Deck / SteamOS and other non-NVIDIA Linux hosts default to CPU-only PyTorch, which avoids downloading NVIDIA CUDA runtime wheels that Whisper does not need there.
- NVIDIA Linux hosts keep the default PyTorch install path.
- Override with `HEIMGEIST_TORCH_FLAVOR=default`, `HEIMGEIST_TORCH_FLAVOR=cpu`, or `HEIMGEIST_TORCH_FLAVOR=rocm6.4`.
- Use `HEIMGEIST_TORCH_INDEX_URL=...` if you need a custom PyTorch wheel index.

Manual startup:

```bash
python3.13 -m venv backend/.venv
backend/.venv/bin/python -m pip install --upgrade pip
# Steam Deck / SteamOS / CPU-only Linux:
# backend/.venv/bin/python -m pip install --index-url https://download.pytorch.org/whl/cpu torch
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm install
npm run dev
```

## Local macOS Packaging

Requirements:

- `backend/.venv` with backend dependencies installed
- Rust/Tauri build prerequisites
- Ollama installed and running separately
- Optional: SearXNG on `http://127.0.0.1:8888`

Build a local macOS app bundle:

```bash
npm run package:mac
```

This builds the React renderer, creates a PyInstaller backend sidecar from `backend/.venv`, bundles ffmpeg/ffprobe helpers when available from npm dependencies, and runs `tauri build --bundles app`.

The `.app` is written to:

```text
src-tauri/target/release/bundle/macos/Heimgeist.app
```

The packaged app starts its own FastAPI backend sidecar on `127.0.0.1:8000`. Ollama remains an external requirement, and SearXNG remains optional.

## File Tree

```text
.
├── backend/
│   ├── main.py
│   ├── sidecar_main.py
│   ├── local_rag.py
│   ├── rag/
│   ├── websearch.py
│   ├── ollama_client.py
│   ├── models.py
│   ├── database.py
│   ├── paths.py
│   ├── schemas.py
│   └── requirements.txt
├── src-tauri/
│   ├── src/main.rs
│   ├── tauri.conf.json
│   ├── binaries/
│   └── capabilities/
├── src/
│   ├── App.jsx
│   ├── LibraryManager.jsx
│   ├── GeneralSettings.jsx
│   ├── InterfaceSettings.jsx
│   ├── WebsearchSettings.jsx
│   ├── markdown.js
│   ├── colorSchemes.js
│   └── styles.css
├── package.json
├── run.sh
├── scripts/
└── vite.config.js
```
