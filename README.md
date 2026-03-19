# Heimgeist

Heimgeist is a local desktop chat client for Ollama. It combines an Electron + React renderer with a FastAPI backend, stores chat history in SQLite, supports optional SearXNG-backed web search, and can enrich prompts with context from local library indexes.

## Features

- Local desktop chat UI with Electron
- Ollama-backed chat with streaming and non-streaming replies
- Persistent chat sessions and automatic title generation
- Edit-and-regenerate flow for earlier user messages
- Optional web search enrichment with source chips
- Local library management for RAG-style prompt enrichment
- Theme selection and UI scale controls

## Local Libraries

The `DBs` tab is no longer a placeholder. You can:

- create and rename libraries
- register files and folders
- attach one library to chat context
- open or remove registered files from the UI

When a library is added to chat, Heimgeist prepares it automatically by building a corpus, enriching the content, and creating retrieval indexes. For each chat turn, Heimgeist queries the active library, turns the top results into a local context block, appends that block to the user prompt, and sends the enriched prompt to Ollama.

## Stack

- Frontend: Electron, React, Vite
- Backend: FastAPI, SQLAlchemy, SQLite
- Search enrichment: SearXNG + page fetching/reranking
- Local RAG pipeline: corpus build, enrichment, embedding, and retrieval helpers under `backend/rag/`

## Development

Requirements:

- Node.js 18+
- Python 3.13
- Ollama running locally
- Optional: SearXNG on `http://localhost:8888`

Quick start:

```bash
./run.sh
```

This creates or refreshes `backend/.venv`, installs Python dependencies, installs npm dependencies, and starts the dev stack.

Manual startup:

```bash
python3.13 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm install
npm run dev
```

## Project Layout

```text
.
├── backend/
│   ├── main.py
│   ├── local_rag.py
│   ├── rag/
│   ├── websearch.py
│   ├── ollama_client.py
│   ├── models.py
│   ├── database.py
│   ├── schemas.py
│   └── requirements.txt
├── electron/
│   ├── main.cjs
│   └── preload.cjs
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
└── vite.config.js
```
