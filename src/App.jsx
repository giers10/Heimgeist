// /Users/giers/Heimgeist/src/App.jsx
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom';
import TextareaAutosize from 'react-textarea-autosize';
import GeneralSettings from './GeneralSettings'
import InterfaceSettings from './InterfaceSettings'
import LibraryManager from './LibraryManager'
import WebsearchSettings from './WebsearchSettings'
import { markdownToHTML  } from './markdown';
import { applyColorScheme } from './colorSchemes'
import {
  loadStoredWebsearchEngines,
  normalizeWebsearchEngines,
} from './websearchEngines'
// Extract <think> or <thinking> block (first occurrence) and return { think, answer }
function splitThinkBlocks(text) {
  if (!text) return { think: null, answer: '' };

  const openTagRe = /<think(?:ing)?>/i;
  const closeTagRe = /<\/think(?:ing)?>/i;

  const openMatch = text.match(openTagRe);

  if (!openMatch) {
    // No opening <think> tag found, so all content is answer
    return { think: null, answer: text };
  }

  const openTagIndex = openMatch.index;
  const openTagLength = openMatch[0].length;

  const answerPartBeforeThink = text.substring(0, openTagIndex).trim();
  let contentAfterOpenTag = text.substring(openTagIndex + openTagLength);

  const closeMatch = contentAfterOpenTag.match(closeTagRe);

  let thinkInner = null;
  let finalAnswer = answerPartBeforeThink;

  if (closeMatch) {
    // Both open and close tags are present
    thinkInner = contentAfterOpenTag.substring(0, closeMatch.index).trim();
    finalAnswer += contentAfterOpenTag.substring(closeMatch.index + closeMatch[0].length);
  } else {
    // Only open tag found (streaming case), take everything after it as think
    thinkInner = contentAfterOpenTag.trim();
  }

  return { think: thinkInner || null, answer: finalAnswer.trim() };
}

// Renders assistant message with a collapsible "Thoughts" block (if present)
function AssistantMessageContent({ content, streamOutput, sources }) {
  const { think, answer } = splitThinkBlocks(content || '');
  const [open, setOpen] = React.useState(false);
  const showThinkButton = !!think;

  return (
    <div className="assistant-message">
      {showThinkButton && (
        <div className="assistant-thoughts">
          <button
            className="think-toggle"
            onClick={() => setOpen(o => !o)}
            aria-expanded={open ? 'true' : 'false'}
            aria-controls="think-content"
          >
            <span className="think-toggle-icon" aria-hidden="true">
              {open ? '▾' : '▸'}
            </span>
            Thoughts
          </button>
          {open && (
            <div
              id="think-content"
              className="think-content"
              dangerouslySetInnerHTML={{ __html: markdownToHTML(think) }}
            />
          )}
        </div>
      )}
      <div
        className="msg-content"
        dangerouslySetInnerHTML={{ __html: markdownToHTML(answer || content || '') }}
      />
      {Array.isArray(sources) && sources.length > 0 && (
        <div className="msg-sources chips">
          {sources.map((u, i) => {
            let label = u;
            let isFile = false;
            try {
              const parsed = new URL(u);
              if (parsed.protocol === 'file:') {
                isFile = true;
                const parts = parsed.pathname.split('/').filter(Boolean);
                label = decodeURIComponent(parts[parts.length - 1] || u);
              } else {
                const host = parsed.hostname || u;
                label = host.replace(/^www\./i, '');
              }
            } catch {}
            return (
              <a
                key={u + i}
                className="chip"
                href={u}
                target="_blank"
                rel="noreferrer"
                title={u}
                onClick={(event) => {
                  if (!isFile) return;
                  event.preventDefault();
                  try {
                    const parsed = new URL(u);
                    window.electronAPI?.openPath?.(decodeURIComponent(parsed.pathname));
                  } catch {}
                }}
              >
                {label}
              </a>
            );
          })}
        </div>
      )}
    </div>
  );
}

const API_URL_KEY = 'backendApiUrl';
const COLOR_SCHEME_KEY = 'colorScheme';
const WEBSEARCH_URL_KEY = 'websearch.searxUrl';
const WEBSEARCH_ENGINES_KEY = 'websearch.engines';
const CHAT_LIBRARY_MAP_KEY = 'chat.libraryBySession';
const DEFAULT_SEARX_URL = 'http://127.0.0.1:8888';

// Initial API value will be set by useEffect after settings are loaded
let API = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000';
const TOP_ALIGN_OFFSET = 48; // match .chat padding + header height for exact top alignment (should be more dynamic depending on header height)
const BOTTOM_EPSILON = 24; // px tolerance for treating as bottom

function resolveBackendApiUrl(settings) {
  return settings.backendApiUrl || settings.ollamaApiUrl || API;
}

function migrateLegacySearxUrl(value) {
  const trimmed = typeof value === 'string' ? value.trim() : '';
  if (!trimmed) return DEFAULT_SEARX_URL;
  if (trimmed === 'http://localhost:8888') return DEFAULT_SEARX_URL;
  return trimmed;
}

export default function App() {
  const [chatSessions, setChatSessions] = useState([])
  const [activeSessionId, setActiveSessionId] = useState(null)
  const [activeSidebarMode, setActiveSidebarMode] = useState('chats') // 'chats', 'dbs', 'settings'
  const [activeSettingsSubmenu, setActiveSettingsSubmenu] = useState('General'); // 'General', 'Interface'
  const [editingSessionId, setEditingSessionId] = useState(null); // ID of the session being edited
  const [editingLibrarySlug, setEditingLibrarySlug] = useState(null)
  const [libraries, setLibraries] = useState([])
  const [libraryJobs, setLibraryJobs] = useState([])
  const [activeLibrarySlug, setActiveLibrarySlug] = useState(null)
  const [chatLibraryBySession, setChatLibraryBySession] = useState(() => {
    try {
      const raw = localStorage.getItem(CHAT_LIBRARY_MAP_KEY)
      return raw ? JSON.parse(raw) : {}
    } catch {
      return {}
    }
  })
  const [isCreatingLibrary, setIsCreatingLibrary] = useState(false)
  const [newLibraryName, setNewLibraryName] = useState('')
  const [libraryCreateError, setLibraryCreateError] = useState('')
  const [isDbPickerOpen, setIsDbPickerOpen] = useState(false)

  // Use currentSessionId for the actual chat operations
  const [model, setModel] = useState('')
  const [input, setInput] = useState('')
  const chatRef = useRef(null)
  const textareaRef = useRef(null); // Ref for the textarea
  const dbPickerRef = useRef(null)
  const [backendApiUrl, setBackendApiUrl] = useState(API); // State for Heimgeist backend URL
  const [colorScheme, setColorScheme] = useState('Default'); // State for color scheme
  const [streamOutput, setStreamOutput] = useState(false);
  const [startupTaskMessage, setStartupTaskMessage] = useState('');
  const [startupTaskBusy, setStartupTaskBusy] = useState(false);
  const [searxUrl, setSearxUrl] = useState(() => migrateLegacySearxUrl(localStorage.getItem(WEBSEARCH_URL_KEY)));
  const [searxEngines, setSearxEngines] = useState(() =>
    loadStoredWebsearchEngines(localStorage.getItem(WEBSEARCH_ENGINES_KEY))
  );
  useEffect(() => {
    localStorage.setItem(WEBSEARCH_URL_KEY, searxUrl || '');
  }, [searxUrl]);

  useEffect(() => {
    try {
      localStorage.setItem(
        WEBSEARCH_ENGINES_KEY,
        JSON.stringify(normalizeWebsearchEngines(searxEngines))
      );
    } catch {}
  }, [searxEngines]);
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [loading, setLoading] = useState(true); // Loading state for initial session fetch
  const [unreadSessions, setUnreadSessions] = useState([]); // Track unread messages
  const [scrollPositions, setScrollPositions] = useState({}); // Store scroll positions for each session
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const startupOllamaCheckRanRef = useRef(false);
  // Editing state for user messages
  const [editingMessageIndex, setEditingMessageIndex] = useState(null);
  const [editText, setEditText] = useState('');
  // Helpers + handlers for message copy/edit/regenerate (must live inside App)
  function getMarkdownForCopy(message) {
    const raw = message.content || '';

    if (message.role === 'assistant') {
      // Copy the assistant's raw *markdown answer*, not rendered text,
      // and strip any <think>...</think> block.
      try {
        const { answer } = splitThinkBlocks(raw);
        return (answer || raw).trim();
      } catch {
        return raw.trim();
      }
    }

    // User messages: copy exactly as typed
    return raw;
  }

  async function handleCopyMessage(message) {
    try {
      await navigator.clipboard.writeText(getMarkdownForCopy(message));
    } catch (err) {
      console.error('Failed to copy message:', err);
    }
  }

  function setAssistantMessageContent(sessionId, messageId, content, options = {}) {
    const { removeIfEmpty = false } = options

    setChatSessions(prevSessions =>
      prevSessions.map(session => {
        if (session.session_id !== sessionId) return session

        const nextMessages = []
        for (const message of session.messages || []) {
          if (message.id !== messageId) {
            nextMessages.push(message)
            continue
          }

          if (removeIfEmpty && !content) continue
          nextMessages.push({ ...message, content })
        }

        return { ...session, messages: nextMessages }
      })
    )
  }

  function isAbortError(error) {
    return error?.name === 'AbortError'
  }

  function getErrorText(error) {
    if (error instanceof Error && error.message) return error.message
    return String(error)
  }

  async function expectBackendJson(response) {
    const data = await response.json().catch(() => null)
    if (response.ok) return data
    const detail = typeof data?.detail === 'string'
      ? data.detail
      : (typeof data?.message === 'string' ? data.message : '')
    throw new Error(detail || `HTTP ${response.status}`)
  }

  async function fetchStartupOllamaStatus() {
    const response = await fetch(`${backendApiUrl}/ollama/startup-status`)
    return expectBackendJson(response)
  }

  async function prepareStartupModels() {
    const response = await fetch(`${backendApiUrl}/startup/prepare-models`, { method: 'POST' })
    return expectBackendJson(response)
  }

  async function fetchLocalLibraryContext(slug, prompt, signal) {
    if (!slug) return { contextBlock: null, sources: [] }

    const resp = await fetch(`${backendApiUrl}/libraries/${slug}/context`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal,
      body: JSON.stringify({
        prompt,
        top_k: 5
      })
    })
    const data = await resp.json()
    return {
      contextBlock: typeof data?.context_block === 'string' && data.context_block.trim() ? data.context_block.trim() : null,
      sources: Array.isArray(data?.sources) ? data.sources : [],
    }
  }

  function startEditMessage(index, content) {
    setEditingMessageIndex(index);
    setEditText(content || '');
  }

  function cancelEditMessage() {
    setEditingMessageIndex(null);
    setEditText('');
  }

  async function commitEditMessage(index) {
    const original = (messages[index]?.content || '').trim();
    const nextRaw = editText ?? '';
    const next = nextRaw.trim();

    // NEW: If empty after trimming, cancel edit (revert to original)
    if (next.length === 0) {
      cancelEditMessage();
      return;
    }

    // If nothing changed, cancel edit
    if (next === original) {
      cancelEditMessage();
      return;
    }

    const sessionId = activeSessionId;
    if (!sessionId) return;

    // Optimistically update UI: set edited content and prune following messages
    setChatSessions(prev =>
      prev.map(s => {
        if (s.session_id !== sessionId) return s;
        const old = s.messages || [];
        const updated = old.slice(0, index + 1).map((m, j) =>
          j === index ? { ...m, content: next } : m
        );
        return { ...s, messages: updated };
      })
    );

    // Exit edit mode immediately
    setEditingMessageIndex(null);
    setEditText('');

    // ⬇️ Scroll the chat frame to the bottom after the DOM updates
    requestAnimationFrame(() => scrollToBottom('auto', sessionId));

    try {
      const resp = await fetch(`${backendApiUrl}/sessions/${sessionId}/messages/${index}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: next })
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    } catch (err) {
      // Roll back to original content on failure
      console.error('Failed to update message:', err);
      setChatSessions(prev =>
        prev.map(s => {
          if (s.session_id !== sessionId) return s;
          const old = s.messages || [];
          const restored = old.map((m, j) =>
            j === index ? { ...m, content: original } : m
          );
          return { ...s, messages: restored };
        })
      );
      return; // don't regenerate on failure
    }

    // Continue conversation from the edited message
    await regenerateFromIndex(index, next);
  }

async function regenerateFromIndex(index, overrideUserText = null) {
  const sessionId = activeSessionId
  if (isSending || !sessionId || typeof index !== 'number') return

  const msgs = (chatSessions.find(s => s.session_id === sessionId)?.messages) || []
  let lastUserIdx = index
  for (let i = index; i >= 0; i--) {
    if (msgs[i]?.role === 'user') {
      lastUserIdx = i
      break
    }
  }

  setChatSessions(prev =>
    prev.map(s => s.session_id === sessionId
      ? { ...s, messages: (s.messages || []).slice(0, lastUserIdx + 1) }
      : s
    )
  )

  const requestController = beginCancelableRequest(sessionId)

  let enrichedPrompt = overrideUserText != null ? overrideUserText : (msgs[lastUserIdx]?.content || '')
  let citationSources = []
  const contextBlocks = []
  try {
    const selectedLibrary = getChatLibraryForSession(sessionId)
    const promptText = overrideUserText != null ? overrideUserText : (msgs[lastUserIdx]?.content || '')

    if (selectedLibrary?.states?.is_indexed) {
      try {
        const localContext = await fetchLocalLibraryContext(selectedLibrary.slug, promptText, requestController.signal)
        if (localContext.contextBlock) {
          contextBlocks.push(localContext.contextBlock)
        }
        if (Array.isArray(localContext.sources)) {
          citationSources.push(...localContext.sources)
        }
      } catch (error) {
        if (isAbortError(error)) throw error
        console.warn('local library enrichment (regenerate) failed', error)
      }
    }

    if (webSearchEnabled) {
      try {
        const historyForSearch = msgs
          .slice(Math.max(0, lastUserIdx - 7), lastUserIdx + 1)
          .map(m => ({ role: m.role, content: m.content || '' }))
        if (historyForSearch.length > 0) {
          historyForSearch[historyForSearch.length - 1] = { role: 'user', content: promptText }
        }

        const resp = await fetch(`${backendApiUrl}/websearch`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: requestController.signal,
          body: JSON.stringify({
            prompt: promptText,
            model,
            messages: historyForSearch,
            history_limit: 8,
            searx_url: searxUrl || null,
            engines: Array.isArray(searxEngines) ? searxEngines : null,
          })
        })
        const data = await resp.json()
        if (data && typeof data.context_block === 'string' && data.context_block.trim()) {
          contextBlocks.push(data.context_block.trim())
        }
        if (Array.isArray(data?.sources)) {
          citationSources.push(...data.sources)
        }
      } catch (error) {
        if (isAbortError(error)) throw error
        console.warn('web search enrichment (regenerate) failed', error)
      }
    }

    citationSources = [...new Set(citationSources)]
    if (contextBlocks.length > 0) {
      enrichedPrompt = `${promptText}\n\n${contextBlocks.join('\n\n')}`
    } else {
      enrichedPrompt = null
    }

    if (streamOutput) {
      const assistantMsgId = `msg-${Date.now()}-${Math.random()}`
      let full = ''

      setChatSessions(prev =>
        prev.map(s => s.session_id === sessionId
          ? { ...s, messages: [...(s.messages || []), { id: assistantMsgId, role: 'assistant', content: '', sources: citationSources }] }
          : s
        )
      )

      try {
        const res = await fetch(`${backendApiUrl}/sessions/${sessionId}/regenerate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: requestController.signal,
          body: JSON.stringify({
            index,
            model,
            stream: true,
            enriched_message: enrichedPrompt,
            sources: citationSources || []
          })
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)

        const reader = res.body?.getReader()
        if (!reader) throw new Error('Missing response body')

        const decoder = new TextDecoder()
        let unreadMarked = false

        while (true) {
          const { value, done } = await reader.read()
          if (done) break

          const chunk = decoder.decode(value, { stream: true })
          full += chunk
          setAssistantMessageContent(sessionId, assistantMsgId, full)

          if (!unreadMarked && activeSessionIdRef.current !== sessionId) {
            unreadMarked = true
            setPendingScrollToLastUser(prev => ({ ...prev, [sessionId]: assistantMsgId }))
            setUnreadSessions(prev => [...new Set([...prev, sessionId])])
          }
        }

        if (activeSessionIdRef.current !== sessionId) {
          setPendingScrollToLastUser(prev => ({ ...prev, [sessionId]: assistantMsgId }))
          setUnreadSessions(prev => [...new Set([...prev, sessionId])])
        } else if (!userScrolledUpRef.current[sessionId]) {
          requestAnimationFrame(() => scrollMessageToTop(assistantMsgId, 'smooth', sessionId))
        } else {
          setNewMsgTip(prev => ({ ...prev, [sessionId]: assistantMsgId }))
        }
      } catch (error) {
        if (isAbortError(error)) {
          setAssistantMessageContent(sessionId, assistantMsgId, full, { removeIfEmpty: true })
          return
        }

        console.error(error)
        setAssistantMessageContent(sessionId, assistantMsgId, `Error: ${getErrorText(error)}`, { removeIfEmpty: true })
        return
      }
    } else {
      const res = await fetch(`${backendApiUrl}/sessions/${sessionId}/regenerate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: requestController.signal,
        body: JSON.stringify({
          index,
          model,
          stream: false,
          enriched_message: enrichedPrompt,
          sources: citationSources || []
        })
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)

      const data = await res.json()
      const assistantMsgId = `msg-${Date.now()}`
      setChatSessions(prev =>
        prev.map(s => s.session_id === sessionId
          ? { ...s, messages: [...(s.messages || []), { role: 'assistant', content: data.reply, id: assistantMsgId, sources: citationSources }] }
          : s
        )
      )

      if (activeSessionIdRef.current !== sessionId) {
        setPendingScrollToLastUser(prev => ({ ...prev, [sessionId]: assistantMsgId }))
        setUnreadSessions(prev => [...new Set([...prev, sessionId])])
      } else if (!userScrolledUpRef.current[sessionId]) {
        requestAnimationFrame(() => scrollMessageToTop(assistantMsgId, 'smooth', sessionId))
      } else {
        setNewMsgTip(prev => ({ ...prev, [sessionId]: assistantMsgId }))
      }
    }
  } catch (error) {
    if (!isAbortError(error)) {
      console.error(error)
    }
  } finally {
    finishCancelableRequest(requestController)
  }
}


  // Persist userScrolledUp state per session + live ref for closures (streaming)
  const [userScrolledUpState, setUserScrolledUpState] = useState({});
  const userScrolledUpRef = useRef({});

  // When a response arrives in a non-active chat, remember to scroll to the new ASSISTANT message on open
  const [pendingScrollToLastUser, setPendingScrollToLastUser] = useState({}); // { [sessionId]: assistantMsgId }

  // Live per-session scrollTop tracker to avoid races
  const scrollTopsRef = useRef({});
  // Live per-session previous scrollTop tracker to detect scroll direction
  const prevScrollTopsRef = useRef({});

  // Tip state: { [sessionId]: messageId }
  const [newMsgTip, setNewMsgTip] = useState({});

  // Collapse state per user message: { [msgKey]: boolean } — true means "collapsed"
  const [collapsedUserMsgs, setCollapsedUserMsgs] = useState({});

  // Compute a stable key for collapse map (prefer id, else session:index)
  const collapseKeyFor = (m, i, sessionId) => (m?.id ? m.id : `${sessionId}:${i}`);

  // Initialize/maintain collapsed map whenever messages or the active session change
  useEffect(() => {
    if (!activeSessionId) return;

    const msgs =
      (chatSessions.find(s => s.session_id === activeSessionId)?.messages) || [];

    setCollapsedUserMsgs(prev => {
      const next = {};
      msgs.forEach((m, i) => {
        if (m.role !== 'user') return;
        const key = collapseKeyFor(m, i, activeSessionId);
        const lineCount = (m.content || '').split(/\r\n|\r|\n/).length;
        const needsCollapse = lineCount > 30;
        // Default collapsed = true when needsCollapse; preserve user toggles
        next[key] = needsCollapse ? (prev[key] ?? true) : false;
      });
      return next;
    });
  }, [chatSessions, activeSessionId]);

  // Toggle collapse/expand for a specific message
  function toggleUserMsgCollapse(key) {
    setCollapsedUserMsgs(prev => ({ ...prev, [key]: !(prev[key] ?? true) }));
  }

  const setUserScrolledUp = React.useCallback((sessionId, value) => {
    setUserScrolledUpState(prev => {
      const next = { ...prev, [sessionId]: value };
      userScrolledUpRef.current = next;
      return next;
    });
  }, []);

  const activeRequestRef = useRef(null);
  const justSentMessage = useRef(false);
  const lastSentSessionRef = useRef(null);
  const activeSessionIdRef = useRef(activeSessionId);
  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  const beginCancelableRequest = React.useCallback((sessionId) => {
    const controller = new AbortController()
    activeRequestRef.current = { controller, sessionId }
    setIsSending(true)
    return controller
  }, [])

  const finishCancelableRequest = React.useCallback((controller) => {
    if (activeRequestRef.current?.controller !== controller) return
    activeRequestRef.current = null
    setIsSending(false)
  }, [])

  const cancelActiveRequest = React.useCallback(() => {
    const activeRequest = activeRequestRef.current
    if (!activeRequest) return
    activeRequestRef.current = null
    activeRequest.controller.abort()
    setIsSending(false)
  }, [])

  useEffect(() => {
    return () => {
      activeRequestRef.current?.controller.abort()
    }
  }, [])

  // Flag to ensure we only restore once per open of a chat
  const restoredForRef = useRef(null);

  // Sidebar resizing state
  const [sidebarWidth, setSidebarWidth] = useState(230);
  const [isResizing, setIsResizing] = useState(false);

  const startResizing = React.useCallback((mouseDownEvent) => {
    setIsResizing(true);
  }, []);

  const stopResizing = React.useCallback(() => {
    setIsResizing(false);
  }, []);

  const resizeSidebar = React.useCallback((mouseMoveEvent) => {
    if (isResizing) {
      const newWidth = Math.max(230, Math.min(500, mouseMoveEvent.clientX));
      setSidebarWidth(newWidth);
    }
  }, [isResizing]);

  React.useEffect(() => {
    window.addEventListener('mousemove', resizeSidebar);
    window.addEventListener('mouseup', stopResizing);
    return () => {
      window.removeEventListener('mousemove', resizeSidebar);
      window.removeEventListener('mouseup', stopResizing);
    };
  }, [resizeSidebar, stopResizing]);

  React.useEffect(() => {
    if (isResizing) {
      document.body.classList.add('no-select');
    } else {
      document.body.classList.remove('no-select');
    }
  }, [isResizing]);

  React.useEffect(() => {
    const onClick = async (e) => {
      const btn = e.target.closest('.codeblock__copy');
      if (!btn) return;

      const wrapper = btn.closest('.codeblock');
      const codeEl = wrapper?.querySelector('pre > code');
      if (!codeEl) return;

      try {
        // Use textContent to copy the plain code accurately
        await navigator.clipboard.writeText(codeEl.textContent || '');
        // Optional: brief visual feedback
        btn.classList.add('copied');
        setTimeout(() => btn.classList.remove('copied'), 800);
      } catch (err) {
        console.error('Copy failed:', err);
      }
    };

    document.addEventListener('click', onClick);
    return () => document.removeEventListener('click', onClick);
  }, []);


  // Load settings on startup
  useEffect(() => {
    window.electronAPI.getSettings().then(settings => {
      setBackendApiUrl(resolveBackendApiUrl(settings));
      setColorScheme(settings.colorScheme || 'Default');
      setModel(settings.chatModel || ''); // Load the selected model, with a fallback
      setStreamOutput(settings.streamOutput || false);
      setScrollPositions(settings.scrollPositions || {}); // Load scroll positions
      applyColorScheme(settings.colorScheme || 'Default'); // Apply initial scheme
    }).finally(() => {
      setSettingsLoaded(true);
    });

    const handleFocus = () => {
      if (activeSidebarMode === 'chats') {
        textareaRef.current?.focus();
      }
    };

    window.electronAPI.onWindowFocus(handleFocus);

    return () => {
      // Clean up the listener when the component unmounts
      // This part is tricky with the current setup, as `onWindowFocus` uses `ipcRenderer.on`
      // which doesn't return a cleanup function. A more robust implementation
      // would involve `ipcRenderer.removeListener`. For now, we'll assume this is okay
      // for the lifetime of the app.
    };
  }, [activeSidebarMode]);

  useEffect(() => {
    if (!settingsLoaded || loading || !backendApiUrl || startupOllamaCheckRanRef.current) return
    startupOllamaCheckRanRef.current = true

    let cancelled = false
    const timerId = window.setTimeout(() => { ;(async () => {
      let actionStarted = false
      try {
        let status = await fetchStartupOllamaStatus()
        if (cancelled) return

        if (!status?.ollama_running && status?.can_manage_locally) {
          const confirmed = window.confirm(
            `Ollama is not running at ${status.ollama_url}. Start it in the background now with "ollama serve"?`
          )
          if (cancelled) return
          if (confirmed) {
            actionStarted = true
            setStartupTaskBusy(true)
            setStartupTaskMessage('Starting Ollama in the background...')
            const response = await fetch(`${backendApiUrl}/ollama/start`, { method: 'POST' })
            status = await expectBackendJson(response)
            if (cancelled) return
          }
        }

        const needsWhisper = !status?.whisper_model_available
        const needsEmbedding = Boolean(status?.ollama_running && status?.can_manage_locally && !status?.embedding_model_available)

        if (needsWhisper || needsEmbedding) {
          actionStarted = true
          setStartupTaskBusy(true)
          if (needsWhisper && needsEmbedding) {
            setStartupTaskMessage(
              `Downloading Whisper ${status?.whisper_model || 'base'} and ${status.selected_embed_model}. This can take a while on first install.`
            )
          } else if (needsWhisper) {
            setStartupTaskMessage(`Downloading Whisper ${status?.whisper_model || 'base'}. This can take a while on first install.`)
          } else {
            setStartupTaskMessage(`Downloading ${status.selected_embed_model} from Ollama. This can take a while on first install.`)
          }
          await prepareStartupModels()
          if (cancelled) return
        }
      } catch (error) {
        if (!cancelled) {
          console.warn('startup Ollama check failed', error)
          if (actionStarted) {
            window.alert(`Startup action failed: ${getErrorText(error)}`)
          }
        }
      } finally {
        if (!cancelled) {
          setStartupTaskBusy(false)
          setStartupTaskMessage('')
        }
      }
    })() }, 1200)

    return () => {
      cancelled = true
      window.clearTimeout(timerId)
    }
  }, [backendApiUrl, loading, settingsLoaded]);

  // Apply color scheme whenever it changes
  useEffect(() => {
    applyColorScheme(colorScheme);
  }, [colorScheme]);

  const fetchHistory = (sessionId) => {
    if (!sessionId || !backendApiUrl) return;
    fetch(`${backendApiUrl}/history?session_id=${encodeURIComponent(sessionId)}`)
      .then(r => r.json())
      .then(data => {
        setChatSessions(prevSessions =>
          prevSessions.map(session =>
            session.session_id === sessionId
              ? { ...session, messages: data.messages || [] }
              : session
          )
        );
      })
      .catch(() => {});
  };

  async function refreshLibraries() {
    if (!backendApiUrl) return;
    try {
      const response = await fetch(`${backendApiUrl}/libraries`);
      const data = await response.json();
      const nextLibraries = Array.isArray(data.libraries) ? data.libraries : [];
      setLibraries(nextLibraries);

      if (nextLibraries.length === 0) {
        setActiveLibrarySlug(null);
        return;
      }

      if (!nextLibraries.some(lib => lib.slug === activeLibrarySlug)) {
        setActiveLibrarySlug(nextLibraries[0].slug);
      }
    } catch (error) {
      console.warn('Failed to load libraries', error);
    }
  }

  async function refreshLibraryJobs() {
    if (!backendApiUrl) return;
    try {
      const response = await fetch(`${backendApiUrl}/jobs`);
      const data = await response.json();
      setLibraryJobs(Array.isArray(data.jobs) ? data.jobs : []);
    } catch (error) {
      console.warn('Failed to load library jobs', error);
    }
  }

  async function createLibrary(nameOverride = null) {
    const rawName = typeof nameOverride === 'string' ? nameOverride : newLibraryName
    const name = rawName.trim()
    if (!name) {
      setLibraryCreateError('Name is required.')
      return
    }
    try {
      setLibraryCreateError('')
      const response = await fetch(`${backendApiUrl}/libraries`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      });
      if (!response.ok) {
        const detail = await response.text()
        throw new Error(detail || `HTTP ${response.status}`)
      }
      const data = await response.json();
      setIsCreatingLibrary(false)
      setNewLibraryName('')
      await refreshLibraries();
      if (data?.slug) {
        setActiveLibrarySlug(data.slug);
      }
    } catch (error) {
      console.error('Failed to create library', error);
      setLibraryCreateError(String(error?.message || error))
    }
  }

  async function handleLibrariesPurged() {
    setLibraries([])
    setLibraryJobs([])
    setActiveLibrarySlug(null)
    setEditingLibrarySlug(null)
    setIsDbPickerOpen(false)
    setChatLibraryBySession({})
    await refreshLibraries()
    await refreshLibraryJobs()
  }

  // Load chat sessions from backend on initial render
  useEffect(() => {
    if (!backendApiUrl) return;
    setLoading(true);
    fetch(`${backendApiUrl}/sessions`)
      .then(r => r.json())
      .then(data => {
        const sessionsWithMessages = data.sessions.map(s => ({ ...s, messages: [] }));
        setChatSessions(sessionsWithMessages);
        if (sessionsWithMessages.length > 0) {
          setActiveSessionId(sessionsWithMessages[0].session_id);
        } else {
          setActiveSessionId(null);
        }
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }, [backendApiUrl]);

  useEffect(() => {
    if (!backendApiUrl) return;
    refreshLibraries();
    refreshLibraryJobs();
  }, [backendApiUrl]);

  useEffect(() => {
    try {
      localStorage.setItem(CHAT_LIBRARY_MAP_KEY, JSON.stringify(chatLibraryBySession || {}));
    } catch {}
  }, [chatLibraryBySession]);

  useEffect(() => {
    if (!backendApiUrl) return;
    const interval = setInterval(() => {
      refreshLibraries();
      refreshLibraryJobs();
    }, 3000);
    return () => clearInterval(interval);
  }, [backendApiUrl, activeSidebarMode, activeLibrarySlug]);

  // Load messages for the active session
  useEffect(() => {
    fetchHistory(activeSessionId);
  }, [activeSessionId, backendApiUrl]);

  useEffect(() => {
    const validSlugs = new Set(libraries.map(library => library.slug))
    setChatLibraryBySession(prev => {
      let changed = false
      const next = {}
      for (const [sessionId, slug] of Object.entries(prev || {})) {
        if (validSlugs.has(slug)) {
          next[sessionId] = slug
        } else {
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [libraries])

  const handleSidebarClick = (mode) => {
    // Saving happens in the centralized cleanup effect below
    setActiveSidebarMode(mode);
  };

  const handleSelectChat = (sessionId) => {
    // Saving happens in the centralized cleanup effect below
    selectChat(sessionId);
  };

  const messages = useMemo(() => {
    return chatSessions.find(s => s.session_id === activeSessionId)?.messages || [];
  }, [activeSessionId, chatSessions]);

  const activeLibrary = useMemo(() => {
    return libraries.find(lib => lib.slug === activeLibrarySlug) || null;
  }, [activeLibrarySlug, libraries]);

  const chatLibrarySlug = activeSessionId ? (chatLibraryBySession[activeSessionId] || null) : null

  const chatLibrary = useMemo(() => {
    return libraries.find(lib => lib.slug === chatLibrarySlug) || null;
  }, [chatLibrarySlug, libraries]);

  const chatLibraryHasActiveJob = useMemo(() => {
    if (!chatLibrarySlug) return false
    return libraryJobs.some(job => job.slug === chatLibrarySlug && (job.status === 'queued' || job.status === 'running'))
  }, [chatLibrarySlug, libraryJobs])

  const chatLibraryStatusSuffix = useMemo(() => {
    if (!chatLibrary) return ''
    if (!chatLibrary.files?.length) return ' (empty)'
    if (chatLibrary.states?.is_indexed) return ''
    return chatLibraryHasActiveJob ? ' (syncing)' : ' (needs sync)'
  }, [chatLibrary, chatLibraryHasActiveJob])

  function getChatLibrarySlugForSession(sessionId) {
    if (!sessionId) return null
    return chatLibraryBySession[sessionId] || null
  }

  function getChatLibraryForSession(sessionId) {
    const slug = getChatLibrarySlugForSession(sessionId)
    if (!slug) return null
    return libraries.find(lib => lib.slug === slug) || null
  }

  function isLibrarySyncing(slug) {
    if (!slug) return false
    return libraryJobs.some(job => job.slug === slug && (job.status === 'queued' || job.status === 'running'))
  }

  function setChatLibraryForSession(sessionId, slug) {
    if (!sessionId) return
    setChatLibraryBySession(prev => {
      const next = { ...(prev || {}) }
      if (slug) {
        next[sessionId] = slug
      } else {
        delete next[sessionId]
      }
      return next
    })
  }

  function removeLibraryFromChatSelections(slug) {
    if (!slug) return
    setChatLibraryBySession(prev => {
      let changed = false
      const next = {}
      for (const [sessionId, librarySlug] of Object.entries(prev || {})) {
        if (librarySlug === slug) {
          changed = true
          continue
        }
        next[sessionId] = librarySlug
      }
      return changed ? next : prev
    })
  }

  useEffect(() => {
    if (!isDbPickerOpen) return

    const onPointerDown = (event) => {
      if (!dbPickerRef.current?.contains(event.target)) {
        setIsDbPickerOpen(false)
      }
    }

    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [isDbPickerOpen])

  useEffect(() => {
    setIsDbPickerOpen(false)
  }, [activeSessionId, activeSidebarMode])

  // Persist the scrollTop of the session we are LEAVING (on chat change or when leaving the chat view)
  useEffect(() => {
    const leavingSessionId = activeSessionId;
    const leavingMode = activeSidebarMode;

    return () => {
      if (leavingMode === 'chats' && leavingSessionId) {
        const top = typeof scrollTopsRef.current[leavingSessionId] === 'number'
          ? scrollTopsRef.current[leavingSessionId]
          : (chatRef.current ? chatRef.current.scrollTop : 0);

        setScrollPositions(prev => {
          const updated = { ...prev, [leavingSessionId]: top };
          window.electronAPI.updateSettings({ scrollPositions: updated });
          return updated;
        });
      }
    };
  }, [activeSessionId, activeSidebarMode]);

  // Track scroll + whether user left bottom
  useEffect(() => {
    const chatDiv = chatRef.current;
    if (!chatDiv) return;

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = chatDiv;
      const isAtBottom = (scrollHeight - scrollTop - clientHeight) <= BOTTOM_EPSILON;

      if (activeSessionId) {
        const prevScrollTop = prevScrollTopsRef.current[activeSessionId];
        const scrolledUp = typeof prevScrollTop === 'number' && scrollTop < prevScrollTop;

        scrollTopsRef.current[activeSessionId] = scrollTop;

        if (isAtBottom) {
          setUserScrolledUp(activeSessionId, false); // User is at bottom, enable autoscroll
        } else if (scrolledUp) {
          setUserScrolledUp(activeSessionId, true); // User scrolled up, disable autoscroll
        }
        // If user scrolled down but not to bottom, maintain current userScrolledUp state
        prevScrollTopsRef.current[activeSessionId] = scrollTop;
      }
    };

    chatDiv.addEventListener('scroll', handleScroll);
    return () => chatDiv.removeEventListener('scroll', handleScroll);
  }, [activeSessionId, setUserScrolledUp]);

  // Auto-hide the tip if user returns to bottom in the active chat
  useEffect(() => {
    const sid = activeSessionId;
    if (!sid) return;
    if (userScrolledUpState[sid] === false) {
      setNewMsgTip(prev => {
        if (!(sid in prev)) return prev;
        const rest = { ...prev };
        delete rest[sid];
        return rest;
      });
    }
  }, [activeSessionId, userScrolledUpState]);

  // --- Robust restoration: do it before paint, exactly once per open ---
  useLayoutEffect(() => {
    if (activeSidebarMode !== 'chats' || !activeSessionId) return;

    const div = chatRef.current;
    if (!div) return;

    restoredForRef.current = null;

    const applyRestore = () => {
      if (restoredForRef.current === activeSessionId) return;

      const liveSaved = typeof scrollTopsRef.current[activeSessionId] === 'number'
        ? scrollTopsRef.current[activeSessionId]
        : undefined;
      const saved = typeof liveSaved === 'number'
        ? liveSaved
        : scrollPositions[activeSessionId];

      if (typeof saved === 'number') {
        div.scrollTop = saved;
        restoredForRef.current = activeSessionId;
        return;
      }
      if (messages.length > 0) {
        // default: bottom when no saved position
        div.scrollTop = div.scrollHeight;
        restoredForRef.current = activeSessionId;
      }
    };

    // Run immediately (pre-paint) and also schedule a fallback rAF
    applyRestore();
    const r0 = requestAnimationFrame(applyRestore);

    // If content size/DOM changes after first paint, apply once
    const onDomChange = () => {
      if (restoredForRef.current !== activeSessionId) {
        requestAnimationFrame(applyRestore);
      }
    };

    const mo = new MutationObserver(onDomChange);
    mo.observe(div, { childList: true, subtree: true });

    const ro = new ResizeObserver(onDomChange);
    ro.observe(div);

    return () => {
      cancelAnimationFrame(r0);
      mo.disconnect();
      ro.disconnect();
    };
  }, [activeSessionId, activeSidebarMode, messages.length, scrollPositions]);

  // If there is no saved scroll and content arrives later (e.g., on first app load),
  // default to bottom exactly once for this open chat.
  useEffect(() => {
    if (activeSidebarMode !== 'chats' || !activeSessionId) return;
    if (restoredForRef.current === activeSessionId) return; // already applied

    const liveSaved = typeof scrollTopsRef.current[activeSessionId] === 'number'
      ? scrollTopsRef.current[activeSessionId]
      : undefined;
    const savedScrollTop = typeof liveSaved === 'number'
      ? liveSaved
      : scrollPositions[activeSessionId];

    // Only when there is no saved position and we now have content
    if (typeof savedScrollTop !== 'number' && messages.length > 0) {
      requestAnimationFrame(() => {
        const div = chatRef.current;
        if (!div) return;
        div.scrollTop = div.scrollHeight;
        restoredForRef.current = activeSessionId;
      });
    }
  }, [messages.length, activeSessionId, activeSidebarMode, scrollPositions]);

  // Session-aware scroll helpers
  const scrollToBottom = (behavior = 'smooth', sessionId = null) => {
    const chatDiv = chatRef.current;
    if (!chatDiv) return;
    const target = sessionId ?? activeSessionIdRef.current;
    if (activeSessionIdRef.current !== target) return;
    chatDiv.scrollTo({ top: chatDiv.scrollHeight, behavior });
    setUserScrolledUp(target, false);
  };

  const scrollMessageToTop = (msgId, behavior = 'auto', sessionId = null) => {
    const chatDiv = chatRef.current;
    if (!chatDiv) return;
    const target = sessionId ?? activeSessionIdRef.current;
    if (activeSessionIdRef.current !== target) return;
    const el = document.getElementById(msgId);
    if (el) {
      const top = Math.max(0, el.offsetTop - TOP_ALIGN_OFFSET);
      chatDiv.scrollTo({ top, behavior });
    }
  };

  // Handler for new message tip click
  const handleNewMsgTipClick = () => {
    const sid = activeSessionIdRef.current;
    const msgId = newMsgTip[sid];
    if (msgId) {
      scrollMessageToTop(msgId, 'smooth', sid);
      setNewMsgTip(prev => {
        const { [sid]: _omit, ...rest } = prev;
        return rest;
      });
    }
  };


async function sendMessage() {
  if (isSending || !input.trim() || !model) return

  let targetSessionId = activeSessionId
  let isNewChat = false
  if (!targetSessionId) {
    const newSession = await createNewChat()
    await new Promise(resolve => setTimeout(resolve, 200))
    targetSessionId = newSession.session_id
    isNewChat = true
  } else {
    const currentSession = chatSessions.find(s => s.session_id === targetSessionId)
    isNewChat = currentSession && currentSession.name === "New Chat" && currentSession.messages.length === 0
  }

  const userMsg = { role: 'user', content: input.trim(), id: `msg-${Date.now()}-${Math.random()}` }
  justSentMessage.current = true
  lastSentSessionRef.current = targetSessionId
  setUserScrolledUp(targetSessionId, false)

  if (activeSessionIdRef.current === targetSessionId) {
    restoredForRef.current = activeSessionIdRef.current
  }

  flushSync(() => {
    setChatSessions(prevSessions =>
      prevSessions.map(session =>
        session.session_id === targetSessionId
          ? { ...session, messages: [...(session.messages || []), userMsg] }
          : session
      )
    )
    setInput('')
  })
  requestAnimationFrame(() => scrollToBottom('auto', targetSessionId))

  const requestController = beginCancelableRequest(targetSessionId)
  try {
    let historyForSearch = []
    try {
      const existing = (chatSessions.find(s => s.session_id === targetSessionId)?.messages) || []
      const lastFew = existing.slice(-8).map(m => ({ role: m.role, content: m.content || '' }))
      historyForSearch = [...lastFew, { role: 'user', content: userMsg.content }]
    } catch {}

    let enrichedPrompt = userMsg.content
    let citationSources = []
    const contextBlocks = []

    const selectedLibrary = getChatLibraryForSession(targetSessionId)

    if (selectedLibrary?.states?.is_indexed) {
      try {
        const localContext = await fetchLocalLibraryContext(selectedLibrary.slug, userMsg.content, requestController.signal)
        if (localContext.contextBlock) {
          contextBlocks.push(localContext.contextBlock)
        }
        if (Array.isArray(localContext.sources)) {
          citationSources.push(...localContext.sources)
        }
      } catch (error) {
        if (isAbortError(error)) throw error
        console.warn('local library enrichment failed', error)
      }
    }

    if (webSearchEnabled) {
      try {
        const resp = await fetch(`${backendApiUrl}/websearch`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: requestController.signal,
          body: JSON.stringify({
            prompt: userMsg.content,
            model,
            messages: historyForSearch,
            history_limit: 8,
            searx_url: searxUrl || null,
            engines: Array.isArray(searxEngines) ? searxEngines : null,
          })
        })
        const data = await resp.json()
        if (data && typeof data.context_block === 'string' && data.context_block.trim()) {
          contextBlocks.push(data.context_block.trim())
        }
        if (Array.isArray(data?.sources)) {
          citationSources.push(...data.sources)
        }
      } catch (error) {
        if (isAbortError(error)) throw error
        console.warn('web search enrichment failed', error)
      }
    }

    citationSources = [...new Set(citationSources)]
    if (contextBlocks.length > 0) {
      enrichedPrompt = `${userMsg.content}\n\n${contextBlocks.join('\n\n')}`
    }

    if (streamOutput) {
      const assistantMsgId = `msg-${Date.now()}-${Math.random()}`
      let fullReply = ''
      const assistantMsg = { role: 'assistant', content: '', id: assistantMsgId, sources: citationSources }
      setChatSessions(prevSessions =>
        prevSessions.map(session =>
          session.session_id === targetSessionId
            ? { ...session, messages: [...(session.messages || []), assistantMsg] }
            : session
        )
      )

      try {
        const res = await fetch(`${backendApiUrl}/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: requestController.signal,
          body: JSON.stringify({
            session_id: targetSessionId,
            model,
            message: userMsg.content,
            enriched_message: contextBlocks.length > 0 ? enrichedPrompt : null,
            stream: true,
            sources: citationSources || []
          })
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)

        const reader = res.body?.getReader()
        if (!reader) throw new Error('Missing response body')

        const decoder = new TextDecoder()
        let pendingMarked = false

        while (true) {
          const { value, done } = await reader.read()
          if (done) {
            setAssistantMessageContent(targetSessionId, assistantMsgId, fullReply)

            if (activeSessionIdRef.current === targetSessionId) {
              if (!userScrolledUpRef.current[targetSessionId]) {
                requestAnimationFrame(() => scrollMessageToTop(assistantMsgId, 'smooth', targetSessionId))
              } else {
                setNewMsgTip(prev => ({ ...prev, [targetSessionId]: assistantMsgId }))
              }
            } else {
              setPendingScrollToLastUser(prev => ({ ...prev, [targetSessionId]: assistantMsgId }))
              setUnreadSessions(prev => [...new Set([...prev, targetSessionId])])
            }

            break
          }

          const chunk = decoder.decode(value, { stream: true })
          fullReply += chunk
          setAssistantMessageContent(targetSessionId, assistantMsgId, fullReply)

          if (activeSessionIdRef.current === targetSessionId && !userScrolledUpRef.current[targetSessionId]) {
            scrollToBottom('auto', targetSessionId)
          }
          if (activeSessionIdRef.current !== targetSessionId && !pendingMarked) {
            setPendingScrollToLastUser(prev => ({ ...prev, [targetSessionId]: assistantMsgId }))
            pendingMarked = true
          }
        }
      } catch (error) {
        if (isAbortError(error)) {
          setAssistantMessageContent(targetSessionId, assistantMsgId, fullReply, { removeIfEmpty: true })
          return
        }

        console.error('Failed to send message:', error)
        setAssistantMessageContent(targetSessionId, assistantMsgId, 'Error: ' + getErrorText(error), { removeIfEmpty: true })
        return
      }
    } else {
      const res = await fetch(`${backendApiUrl}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: requestController.signal,
        body: JSON.stringify({
          session_id: targetSessionId,
          model,
          message: userMsg.content,
          enriched_message: contextBlocks.length > 0 ? enrichedPrompt : null,
          stream: false,
          sources: citationSources || []
        })
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)

      const data = await res.json()
      const assistantMsgId = `msg-${Date.now()}`
      const assistantMsg = {
        role: 'assistant',
        content: data.reply,
        id: assistantMsgId,
        sources: citationSources
      }

      setChatSessions(prevSessions =>
        prevSessions.map(session =>
          session.session_id === targetSessionId
            ? { ...session, messages: [...(session.messages || []), assistantMsg] }
            : session
        )
      )

      if (assistantMsgId) {
        if (activeSessionIdRef.current === targetSessionId) {
          if (!userScrolledUpRef.current[targetSessionId]) {
            requestAnimationFrame(() => scrollMessageToTop(assistantMsgId, 'smooth', targetSessionId))
          } else {
            setNewMsgTip(prev => ({ ...prev, [targetSessionId]: assistantMsgId }))
          }
        } else {
          setPendingScrollToLastUser(prev => ({ ...prev, [targetSessionId]: assistantMsgId }))
        }
      }
    }

    if (activeSessionIdRef.current !== targetSessionId) {
      setUnreadSessions(prev => [...new Set([...prev, targetSessionId])])
    }

    if (isNewChat) {
      fetch(`${backendApiUrl}/generate-title`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: targetSessionId,
          message: userMsg.content,
          model
        })
      })
      .then(r => r.json())
      .then(data => {
        const sanitizedTitle = data.title.replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/i, '').trim()
        setChatSessions(prevSessions =>
          prevSessions.map(session =>
            session.session_id === targetSessionId ? { ...session, name: sanitizedTitle } : session
          )
        )
      })
    }
  } catch (error) {
    if (isAbortError(error)) {
      finishCancelableRequest(requestController)
      return
    }

    console.error('Failed to send message:', error)
    const errorMsg = { role: 'assistant', content: 'Error: ' + getErrorText(error), id: `msg-${Date.now()}-${Math.random()}` }
    setChatSessions(prevSessions =>
      prevSessions.map(session =>
        session.session_id === targetSessionId
          ? { ...session, messages: [...session.messages, errorMsg] }
          : session
      )
    )
  } finally {
    finishCancelableRequest(requestController)
  }
}

  

function toggleWebSearch() {
  setWebSearchEnabled(prev => !prev);
}

async function createNewChat() {
    const newSessionId = 'sess-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    const res = await fetch(`${backendApiUrl}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: newSessionId })
    });
    const newSession = await res.json();
    const sessionWithMessages = { ...newSession, messages: [] };
    setChatSessions(prevSessions => [sessionWithMessages, ...prevSessions]);
    setActiveSessionId(newSession.session_id);
    textareaRef.current?.focus();
    return newSession;
  }

  function selectChat(sessionId) {
    setActiveSessionId(sessionId);
    // Clear unread dot immediately for this chat
    setUnreadSessions(prev => prev.filter(id => id !== sessionId));

    // If we had queued a guided scroll for this chat (from background replies), run it now, smoothly
    const pendingId = pendingScrollToLastUser[sessionId];
    if (pendingId) {
      // Defer until the chat content renders; restoration is gated by restoredForRef, so won't fight
      requestAnimationFrame(() => {
        let tries = 12; // ~200ms @ 60fps
        const attempt = () => {
          const chatDiv = chatRef.current;
          if (!chatDiv) return;

          let el = document.getElementById(pendingId);
          if (!el) {
            const sess = chatSessions.find(s => s.session_id === sessionId);
            if (sess && Array.isArray(sess.messages)) {
              for (let i = sess.messages.length - 1; i >= 0; i--) {
                const m = sess.messages[i];
                if (m.role === 'assistant' && m.id) { el = document.getElementById(m.id); break; }
              }
            }
          }

          if (el) {
            scrollMessageToTop(el.id, 'smooth', sessionId);
            setPendingScrollToLastUser(prev => {
              const { [sessionId]: _omit, ...rest } = prev;
              return rest;
            });
          } else if (tries-- > 0) {
            requestAnimationFrame(attempt);
          }
        };
        requestAnimationFrame(attempt);
      });
    }
  }

  function handleRename(sessionId, newName) {
    fetch(`${backendApiUrl}/sessions/${sessionId}/rename`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: newName })
    })
    .then(() => {
      setChatSessions(prevSessions =>
        prevSessions.map(session =>
          session.session_id === sessionId ? { ...session, name: newName } : session
        )
      );
      setEditingSessionId(null);
    });
  }

  function handleLibraryRename(slug, newName) {
    const name = (newName || '').trim()
    const library = libraries.find(item => item.slug === slug)
    if (!library) {
      setEditingLibrarySlug(null)
      return
    }
    if (!name || name === library.name) {
      setEditingLibrarySlug(null)
      return
    }

    fetch(`${backendApiUrl}/libraries/${slug}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    })
    .then(() => {
      setLibraries(prevLibraries =>
        prevLibraries.map(item =>
          item.slug === slug ? { ...item, name } : item
        )
      )
      setEditingLibrarySlug(null)
    })
  }

  function handleDelete(sessionId) {
    fetch(`${backendApiUrl}/sessions/${sessionId}`, { method: 'DELETE' })
    .then(() => {
      const newSessions = chatSessions.filter(s => s.session_id !== sessionId);
      setChatSessions(newSessions);
      setChatLibraryBySession(prev => {
        const next = { ...(prev || {}) }
        delete next[sessionId]
        return next
      })
      if (activeSessionId === sessionId) {
        setActiveSessionId(newSessions.length > 0 ? newSessions[0].session_id : null);
      }
    });
  }

  function handleLibraryDelete(slug) {
    fetch(`${backendApiUrl}/libraries/${slug}`, { method: 'DELETE' })
    .then(async (response) => {
      if (!response.ok) {
        const detail = await response.text()
        throw new Error(detail || `HTTP ${response.status}`)
      }

      const nextLibraries = libraries.filter(library => library.slug !== slug)
      setLibraries(nextLibraries)
      setLibraryJobs(prevJobs => prevJobs.filter(job => job.slug !== slug))
      setEditingLibrarySlug(current => current === slug ? null : current)
      if (activeLibrarySlug === slug) {
        setActiveLibrarySlug(nextLibraries[0]?.slug || null)
      }
      removeLibraryFromChatSelections(slug)
    })
    .catch((error) => {
      console.error('Failed to delete library', error)
    })
  }

  // Auto-delete empty "New Chat" sessions
  useEffect(() => {
    const emptyNewChats = chatSessions.filter(
      s => s.name === "New Chat" && s.session_id !== activeSessionId && s.messages.length === 0
    );
    if (emptyNewChats.length > 0) {
      emptyNewChats.forEach(chat => {
        handleDelete(chat.session_id);
      });
    }
  }, [activeSessionId, chatSessions, backendApiUrl]);

  const handleChatFrameClick = (e) => {
    const selection = window.getSelection();
    if (selection.toString().length > 0) {
      return;
    }

    if (document.activeElement === textareaRef.current) {
      return;
    }

    if (e.target.closest('.msg')) {
      return;
    }

    textareaRef.current?.focus();
  };

  return (
    <div className="app" style={{ gridTemplateColumns: `${sidebarWidth}px 1fr` }}>
      <div className="sidebar">
        <div className="sidebar-header">
          <div
            className={`sidebar-tab ${activeSidebarMode === 'chats' ? 'active' : ''}`}
            onClick={() => handleSidebarClick('chats')}
          >
            Chats
          </div>
          <div
            className={`sidebar-tab ${activeSidebarMode === 'dbs' ? 'active' : ''}`}
            onClick={() => handleSidebarClick('dbs')}
          >
            DBs
          </div>
          <div
            className={`sidebar-tab ${activeSidebarMode === 'settings' ? 'active' : ''}`}
            onClick={() => handleSidebarClick('settings')}
          >
            Settings
          </div>
        </div>
        <div className="sidebar-content">
          {activeSidebarMode === 'chats' && (
            <div className="chat-list">
              {chatSessions.map(session => (
                <div
                  key={session.session_id}
                  className={`chat-item ${session.session_id === activeSessionId ? 'active' : ''}`}
                  onClick={() => handleSelectChat(session.session_id)}
                >
                  {editingSessionId === session.session_id ? (
                    <input
                      type="text"
                      className="rename-input"
                      defaultValue={session.name}
                      onBlur={() => setEditingSessionId(null)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          handleRename(session.session_id, e.target.value);
                        } else if (e.key === 'Escape') {
                          setEditingSessionId(null);
                        }
                      }}
                      autoFocus
                    />
                  ) : (
                    <>
                      <span>{session.name}</span>
                      <div className="chat-item-buttons">
                        {unreadSessions.includes(session.session_id) && <div className="unread-dot"></div>}
                        <button className="icon-button" onClick={(e) => { e.stopPropagation(); setEditingSessionId(session.session_id); }}>
                          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="feather feather-edit-2"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"></path></svg>
                        </button>
                        <button className="icon-button" onClick={(e) => { e.stopPropagation(); handleDelete(session.session_id); }}>
                          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="feather feather-x"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                        </button>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
          {activeSidebarMode === 'dbs' && (
            <div className="db-list">
              {libraries.length === 0 ? (
                <div className="empty-list-message">No databases yet.</div>
              ) : (
                libraries.map(library => (
                  <div
                    key={library.slug}
                    className={`chat-item ${library.slug === activeLibrarySlug ? 'active' : ''}`}
                    onClick={() => setActiveLibrarySlug(library.slug)}
                  >
                    {editingLibrarySlug === library.slug ? (
                      <input
                        type="text"
                        className="rename-input"
                        defaultValue={library.name}
                        onBlur={() => setEditingLibrarySlug(null)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            handleLibraryRename(library.slug, e.target.value)
                          } else if (e.key === 'Escape') {
                            setEditingLibrarySlug(null)
                          }
                        }}
                        autoFocus
                      />
                    ) : (
                      <>
                        <span>{library.name}</span>
                        <div className="chat-item-buttons">
                          {chatLibrarySlug === library.slug && <div className="db-active-badge">Chat</div>}
                          {isLibrarySyncing(library.slug) && <div className="db-active-badge">Syncing</div>}
                          <button className="icon-button" onClick={(e) => { e.stopPropagation(); setEditingLibrarySlug(library.slug) }}>
                            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="feather feather-edit-2"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"></path></svg>
                          </button>
                          <button className="icon-button" onClick={(e) => { e.stopPropagation(); handleLibraryDelete(library.slug) }}>
                            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="feather feather-x"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                ))
              )}
            </div>
          )}
          {activeSidebarMode === 'settings' && (
            <div className="settings-list">
              <div
                className={`settings-item ${activeSettingsSubmenu === 'General' ? 'active' : ''}`}
                onClick={() => setActiveSettingsSubmenu('General')}
              >
                General
              </div>
              <div
                className={`settings-item ${activeSettingsSubmenu === 'Interface' ? 'active' : ''}`}
                onClick={() => setActiveSettingsSubmenu('Interface')}
              >
                Interface
              </div>
              <div
                className={`settings-item ${activeSettingsSubmenu === 'Websearch' ? 'active' : ''}`}
                onClick={() => setActiveSettingsSubmenu('Websearch')}
              >
                Websearch
              </div>
            </div>
          )}
        </div>
        {activeSidebarMode !== 'settings' && (
          <div className="sidebar-footer">
            {activeSidebarMode === 'chats' && (
              <button className="button new-chat-button" onClick={createNewChat}>New Chat</button>
            )}
            {activeSidebarMode === 'dbs' && (
              isCreatingLibrary ? (
                <div className="new-db-form">
                  <input
                    type="text"
                    className="rename-input"
                    value={newLibraryName}
                    onChange={(e) => setNewLibraryName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        createLibrary()
                      } else if (e.key === 'Escape') {
                        setIsCreatingLibrary(false)
                        setNewLibraryName('')
                        setLibraryCreateError('')
                      }
                    }}
                    placeholder="Database name"
                    autoFocus
                  />
                  {libraryCreateError && <div className="form-error">{libraryCreateError}</div>}
                  <div className="new-db-actions">
                    <button className="button new-db-button" onClick={() => createLibrary()}>Create</button>
                    <button
                      className="button ghost"
                      onClick={() => {
                        setIsCreatingLibrary(false)
                        setNewLibraryName('')
                        setLibraryCreateError('')
                      }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  className="button new-db-button"
                  onClick={() => {
                    setIsCreatingLibrary(true)
                    setLibraryCreateError('')
                  }}
                >
                  New Database
                </button>
              )
            )}
          </div>
        )}
        <div className="resizer" onMouseDown={startResizing}></div>
      </div>
      <div className="main-content">
        {startupTaskMessage && (
          <div className="startup-task-banner" role="status" aria-live="polite">
            {startupTaskBusy && <div className="spinner startup-task-banner__spinner"></div>}
            <div className="startup-task-banner__text">{startupTaskMessage}</div>
          </div>
        )}
        {activeSidebarMode === 'chats' && (
          <>
            <div className="header">
              <strong>Chat - {chatSessions.find(s => s.session_id === activeSessionId)?.name || 'New Chat'}</strong>
              {chatLibrary && (
                <span className="header-subtle">
                  {`DB: ${chatLibrary.name}${chatLibraryStatusSuffix}`}
                </span>
              )}
            </div>

            <div key={activeSessionId} className="chat" ref={chatRef} onClick={handleChatFrameClick}>
              {messages.map((m, i) => {
                const isEditingThis = m.role === 'user' && editingMessageIndex === i;
                return (
                  <div
                    key={m.id || i}
                    id={m.id}
                    className={
                      'msg ' +
                      (m.role === 'user' ? 'user' : 'assistant') +
                      (isEditingThis ? ' editing' : '')
                    }
                  >
                    {m.role === 'assistant' ? (
                      <div className="assistant-message-wrapper">
                        <AssistantMessageContent content={m.content} streamOutput={streamOutput} sources={m.sources} />
                        {!isSending && (
                          <div className="message-options-bar assistant-options">
                            <button className="icon-button" title="Copy message" onClick={() => handleCopyMessage(m)}>
                              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                            </button>
                            <button className="icon-button" title="Regenerate response" onClick={() => regenerateFromIndex(i)}>
                              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.3"></path></svg>
                            </button>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="user-message-wrapper">
                        {isEditingThis ? (
                          <div className="msg-content msg-content--user editing">
                            <div className="user-edit-shadow" aria-hidden="true">
                              {editText}
                            </div>

                            <TextareaAutosize
                              className="edit-message-input edit-overlay"
                              value={editText}
                              onChange={(e) => setEditText(e.target.value)}
                              onBlur={cancelEditMessage}
                              onKeyDown={(e) => {
                                if (e.key === 'Escape') { e.preventDefault(); cancelEditMessage(); }
                                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commitEditMessage(i); }
                              }}
                              autoFocus
                              minRows={1}
                            />
                          </div>
                        ) : (
                          (() => {
                            const raw = m.content || '';
                            const lines = raw.split(/\r\n|\r|\n/);
                            const needsCollapse = lines.length > 30;
                            const key = collapseKeyFor(m, i, activeSessionId);
                            const isCollapsed = needsCollapse ? (collapsedUserMsgs[key] ?? true) : false;
                            const displayText = isCollapsed ? lines.slice(0, 30).join('\n') + '\n…' : raw;

                            return (
                              <>
                                <div className="msg-content msg-content--user">{displayText}</div>
                                {needsCollapse && (
                                  <button
                                    className="user-msg-expand"
                                    onClick={() => toggleUserMsgCollapse(key)}
                                    aria-expanded={isCollapsed ? 'false' : 'true'}
                                  >
                                    {isCollapsed ? 'Show entire message' : 'Collapse'}
                                  </button>
                                )}
                              </>
                            );
                          })()
                        )}
                        {!isSending && !isEditingThis && (
                          <div className="message-options-bar user-options">
                            <button className="icon-button" title="Edit message" onClick={() => startEditMessage(i, m.content)}>
                              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
                            </button>
                            <button className="icon-button" title="Copy message" onClick={() => handleCopyMessage(m)}>
                              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {/* New message tip (active chat only) */}
            {newMsgTip[activeSessionId] && (
              <button
                className="new-msg-tip"
                onClick={handleNewMsgTipClick}
                title="Jump to the new message"
                aria-label="Jump to the new message"
              >
                New message<span style={{ marginLeft: 6 }}>↓</span>
              </button>
            )}

            <div className="footer">
              <div className="footer-content-wrapper">
                <TextareaAutosize
                  ref={textareaRef}
                  className="input"
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      sendMessage();
                    }
                  }}
                  placeholder="Ask any question..."
                  maxRows={13}
                />
                <div className="footer-tool-group" ref={dbPickerRef}>
                  <button
                    type="button"
                    className={"db-picker-toggle" + (chatLibrary ? " active" : "")}
                    onClick={() => {
                      if (!activeSessionId) return
                      setIsDbPickerOpen(prev => !prev)
                    }}
                    title={chatLibrary ? `Database: ${chatLibrary.name}${chatLibraryStatusSuffix}` : 'Select database for this chat'}
                    aria-haspopup="menu"
                    aria-expanded={isDbPickerOpen}
                    disabled={!activeSessionId}
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
                         stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                         aria-hidden="true">
                      <ellipse cx="12" cy="5" rx="8" ry="3"/>
                      <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/>
                      <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>
                    </svg>
                  </button>
                  {isDbPickerOpen && (
                    <div className="db-picker-menu" role="menu">
                      <button
                        type="button"
                        className={"db-picker-option" + (!chatLibrarySlug ? " selected" : "")}
                        onClick={() => {
                          setChatLibraryForSession(activeSessionId, null)
                          setIsDbPickerOpen(false)
                        }}
                      >
                        <span>No database</span>
                        {!chatLibrarySlug && <span className="db-picker-status">Selected</span>}
                      </button>
                      {libraries.length === 0 ? (
                        <div className="db-picker-empty">No databases yet.</div>
                      ) : (
                        libraries.map(library => {
                          const selected = chatLibrarySlug === library.slug
                          const syncing = isLibrarySyncing(library.slug)
                          const status = !library.files?.length
                            ? 'Empty'
                            : library.states?.is_indexed
                              ? 'Ready'
                              : syncing
                                ? 'Syncing'
                                : 'Needs sync'

                          return (
                            <button
                              key={library.slug}
                              type="button"
                              className={"db-picker-option" + (selected ? " selected" : "")}
                              disabled={!library.files?.length}
                              onClick={() => {
                                setChatLibraryForSession(activeSessionId, library.slug)
                                setIsDbPickerOpen(false)
                              }}
                            >
                              <span>{library.name}</span>
                              <span className="db-picker-status">{selected ? 'Selected' : status}</span>
                            </button>
                          )
                        })
                      )}
                    </div>
                  )}
                </div>
                  <button
                    type="button"
                    className={"websearch-toggle" + (webSearchEnabled ? " active" : "")}
                    onClick={toggleWebSearch}
                    title="Toggle web search"
                    aria-pressed={webSearchEnabled}
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
                         stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                         aria-hidden="true">
                      <circle cx="12" cy="12" r="10"/>
                      <line x1="2" y1="12" x2="22" y2="12"/>
                      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                    </svg>
                  </button>
                <button
                  className="button"
                  onClick={isSending ? cancelActiveRequest : sendMessage}
                  title={isSending ? 'Cancel generation' : 'Send'}
                  aria-label={isSending ? 'Cancel generation' : 'Send'}
                >
                  {isSending ? <div className="spinner"></div> : 'Send'}
                </button>
              </div>
            </div>
          </>
        )}
        {activeSidebarMode === 'dbs' && (
          <>
            <div className="header">
              <strong>{activeLibrary?.name || 'Databases'}</strong>
              {chatLibrary && (
                <span className="header-subtle">
                  {`Current chat DB: ${chatLibrary.name}${chatLibraryStatusSuffix}`}
                </span>
              )}
            </div>
            <LibraryManager
              apiBase={backendApiUrl}
              library={activeLibrary}
              jobs={libraryJobs}
              onRefresh={async () => {
                await refreshLibraries();
                await refreshLibraryJobs();
              }}
            />
          </>
        )}
        {activeSidebarMode === 'settings' && (
          <>
            <div className="header">
              <strong>{activeSettingsSubmenu} Settings</strong>
            </div>
            {activeSettingsSubmenu === 'General' && (
              <GeneralSettings
                onModelChange={setModel}
                streamOutput={streamOutput}
                onStreamOutputChange={setStreamOutput}
                onLibrariesPurged={handleLibrariesPurged}
              />
            )}
            {activeSettingsSubmenu === 'Interface' && <InterfaceSettings />}
            {activeSettingsSubmenu === 'Websearch' && (
              <WebsearchSettings
                searxUrl={searxUrl}
                setSearxUrl={setSearxUrl}
                engines={searxEngines}
                setEngines={(next) => setSearxEngines(normalizeWebsearchEngines(next))}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}
