// /Users/giers/Heimgeist/src/App.jsx
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom';
import TextareaAutosize from 'react-textarea-autosize';
import GeneralSettings from './GeneralSettings'
import InterfaceSettings from './InterfaceSettings'
import { markdownToHTML } from './markdown';

const API_URL_KEY = 'ollamaApiUrl';
const COLOR_SCHEME_KEY = 'colorScheme';

// Initial API value will be set by useEffect after settings are loaded
let API = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000';
const TOP_ALIGN_OFFSET = 48; // match .chat padding + header height for exact top alignment (should be more dynamic depending on header height)
const BOTTOM_EPSILON = 24; // px tolerance for treating as bottom

export default function App() {
  const [chatSessions, setChatSessions] = useState([])
  const [activeSessionId, setActiveSessionId] = useState(null)
  const [activeSidebarMode, setActiveSidebarMode] = useState('chats') // 'chats', 'dbs', 'settings'
  const [activeSettingsSubmenu, setActiveSettingsSubmenu] = useState('General'); // 'General', 'Interface'
  const [editingSessionId, setEditingSessionId] = useState(null); // ID of the session being edited

  // Use currentSessionId for the actual chat operations
  const [model, setModel] = useState('')
  const [input, setInput] = useState('')
  const chatRef = useRef(null)
  const textareaRef = useRef(null); // Ref for the textarea
  const [ollamaApiUrl, setOllamaApiUrl] = useState(API); // State for Ollama API URL
  const [colorScheme, setColorScheme] = useState('Default'); // State for color scheme
  const [streamOutput, setStreamOutput] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [loading, setLoading] = useState(true); // Loading state for initial session fetch
  const [unreadSessions, setUnreadSessions] = useState([]); // Track unread messages
  const [scrollPositions, setScrollPositions] = useState({}); // Store scroll positions for each session

  // Persist userScrolledUp state per session + live ref for closures (streaming)
  const [userScrolledUpState, setUserScrolledUpState] = useState({});
  const userScrolledUpRef = useRef({});

  // When a response arrives in a non-active chat, remember to scroll to the new ASSISTANT message on open
  const [pendingScrollToLastUser, setPendingScrollToLastUser] = useState({}); // { [sessionId]: assistantMsgId }

  // Live per-session scrollTop tracker to avoid races
  const scrollTopsRef = useRef({});

  // Tip state: { [sessionId]: messageId }
  const [newMsgTip, setNewMsgTip] = useState({});

  const setUserScrolledUp = React.useCallback((sessionId, value) => {
    setUserScrolledUpState(prev => {
      const next = { ...prev, [sessionId]: value };
      userScrolledUpRef.current = next;
      return next;
    });
  }, []);

  const activeRequestSessionId = useRef(null);
  const justSentMessage = useRef(false);
  const lastSentSessionRef = useRef(null);
  const activeSessionIdRef = useRef(activeSessionId);
  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

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

  // Load settings on startup
  useEffect(() => {
    window.electronAPI.getSettings().then(settings => {
      setOllamaApiUrl(settings.ollamaApiUrl);
      setColorScheme(settings.colorScheme);
      setModel(settings.chatModel || ''); // Load the selected model, with a fallback
      setStreamOutput(settings.streamOutput || false);
      setScrollPositions(settings.scrollPositions || {}); // Load scroll positions
      applyColorScheme(settings.colorScheme); // Apply initial scheme
    });
  }, []);

  // Apply color scheme whenever it changes
  useEffect(() => {
    applyColorScheme(colorScheme);
  }, [colorScheme]);

  // Function to apply color scheme
  const colorSchemes = {
    'Default': {
      '--bg': '#0b1020',
      '--panel': '#141b34',
      '--text': '#e6e8ef',
      '--muted': '#9aa3b2',
      '--accent': '#6ea8fe',
      '--border': '#24304f',
      '--input-bg': '#0e1530',
      '--user-msg-bg': '#111933',
      '--assistant-msg-bg': '#101927',
    },
    'Grayscale': {
      '--bg': '#1a1a1a',
      '--panel': '#2a2a2a',
      '--text': '#f0f0f0',
      '--muted': '#aaaaaa',
      '--accent': '#888888',
      '--border': '#4a4a4a',
      '--input-bg': '#202020',
      '--user-msg-bg': '#333333',
      '--assistant-msg-bg': '#252525',
    },
    'Rose': {
      '--bg': '#200a10',
      '--panel': '#301a20',
      '--text': '#ffe0e0',
      '--muted': '#a09090',
      '--accent': '#E91E63',
      '--border': '#402025',
      '--input-bg': '#2a1015',
      '--user-msg-bg': '#331119',
      '--assistant-msg-bg': '#271019',
    },
  };

  function applyColorScheme(schemeName) {
    const scheme = colorSchemes[schemeName];
    if (scheme) {
      for (const [key, value] of Object.entries(scheme)) {
        document.documentElement.style.setProperty(key, value);
      }
    }
  }

  const fetchHistory = (sessionId) => {
    if (!sessionId || !ollamaApiUrl) return;
    fetch(`${ollamaApiUrl}/history?session_id=${encodeURIComponent(sessionId)}`)
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

  // Load chat sessions from backend on initial render
  useEffect(() => {
    if (!ollamaApiUrl) return;
    setLoading(true);
    fetch(`${ollamaApiUrl}/sessions`)
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
  }, [ollamaApiUrl]);

  // Load messages for the active session
  useEffect(() => {
    fetchHistory(activeSessionId);
  }, [activeSessionId]);

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
        scrollTopsRef.current[activeSessionId] = scrollTop;
      }
      setUserScrolledUp(activeSessionId, !isAtBottom);
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
    if (!input.trim() || !model) return;

    let targetSessionId = activeSessionId;
    let isNewChat = false;
    if (!targetSessionId) {
      const newSession = await createNewChat();
      await new Promise(resolve => setTimeout(resolve, 200));
      targetSessionId = newSession.session_id;
      isNewChat = true;
    } else {
      const currentSession = chatSessions.find(s => s.session_id === targetSessionId);
      isNewChat = currentSession && currentSession.name === "New Chat" && currentSession.messages.length === 0;
    }
    
    const userMsg = { role: 'user', content: input.trim(), id: `msg-${Date.now()}-${Math.random()}` };
    justSentMessage.current = true;
    lastSentSessionRef.current = targetSessionId;
    setUserScrolledUp(targetSessionId, false);

    // Cancel any pending restore for the active session (we're about to control the scroll)
    if (activeSessionIdRef.current === targetSessionId) {
      restoredForRef.current = activeSessionIdRef.current; // mark as already restored
    }

    // Optimistic add and flush DOM, then scroll to bottom
    flushSync(() => {
      setChatSessions(prevSessions =>
        prevSessions.map(session =>
          session.session_id === targetSessionId
            ? { ...session, messages: [...(session.messages || []), userMsg] }
            : session
        )
      );
      setInput('');
    });
    requestAnimationFrame(() => scrollToBottom('auto', targetSessionId));

    setIsSending(true);
    try {
      if (streamOutput) {
        const assistantMsgId = `msg-${Date.now()}-${Math.random()}`;
        const assistantMsg = { role: 'assistant', content: '', id: assistantMsgId };
        setChatSessions(prevSessions =>
          prevSessions.map(session =>
            session.session_id === targetSessionId
              ? { ...session, messages: [...(session.messages || []), assistantMsg] }
              : session
          )
        );

        (async () => {
          try {
            const res = await fetch(`${ollamaApiUrl}/chat`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                session_id: targetSessionId,
                model,
                message: userMsg.content,
                stream: true
              })
            });

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let fullReply = '';
            let pendingMarked = false;

            while (true) {
              const { value, done } = await reader.read();
              if (done) {
                setChatSessions(prevSessions =>
                  prevSessions.map(session =>
                    session.session_id === targetSessionId
                      ? {
                          ...session,
                          messages: session.messages.map(m =>
                            m.id === assistantMsgId ? { ...m, content: fullReply } : m
                          )
                        }
                      : session
                  )
                );

                if (activeSessionIdRef.current === targetSessionId) {
                  if (!userScrolledUpRef.current[targetSessionId]) {
                    // user stayed at bottom -> reveal the message immediately
                    requestAnimationFrame(() => scrollMessageToTop(assistantMsgId, 'smooth', targetSessionId));
                  } else {
                    // user scrolled away while it was generating -> show tip instead of auto-scroll
                    setNewMsgTip(prev => ({ ...prev, [targetSessionId]: assistantMsgId }));
                  }
                } else {
                  setPendingScrollToLastUser(prev => ({ ...prev, [targetSessionId]: assistantMsgId }));
                  setUnreadSessions(prev => [...new Set([...prev, targetSessionId])]);
                }

                break;
              }
              const chunk = decoder.decode(value, { stream: true });
              fullReply += chunk;
              const messageElement = document.getElementById(assistantMsgId)?.firstChild;
              if (messageElement) {
                messageElement.innerHTML = markdownToHTML(fullReply);
              }
              // Keep sticky-bottom *only* when streaming in the active chat and user is at/near bottom.
              // This restores the old "push down while generating" behavior without fighting user scrolls.
              if (
                activeSessionIdRef.current === targetSessionId &&
                !userScrolledUpRef.current[targetSessionId]
              ) {
                // use 'auto' so it stays snappy during streaming
                scrollToBottom('auto', targetSessionId);
              }
              // If streaming in a background chat, prepare a one-time guided scroll
              if (activeSessionIdRef.current !== targetSessionId && !pendingMarked) {
                setPendingScrollToLastUser(prev => ({ ...prev, [targetSessionId]: assistantMsgId }));
                pendingMarked = true;
              }
            }
          } catch (e) {
            console.error("Failed to send message:", e);
            const errorMsg = { role: 'assistant', content: 'Error: ' + e.message, id: `msg-${Date.now()}-${Math.random()}` };
            setChatSessions(prevSessions =>
              prevSessions.map(session =>
                session.session_id === targetSessionId
                  ? { ...session, messages: [...session.messages.slice(0, -1), errorMsg] }
                  : session
              )
            );
          } finally {
            setIsSending(false);
          }
        })();
      } else {
        const res = await fetch(`${ollamaApiUrl}/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: targetSessionId,
            model,
            message: userMsg.content,
            stream: false
          })
        });
        const data = await res.json();
        const assistantMsgId = `msg-${Date.now()}`;
        const assistantMsg = { role: 'assistant', content: data.reply, id: assistantMsgId };

        setChatSessions(prevSessions =>
          prevSessions.map(session =>
            session.session_id === targetSessionId
              ? { ...session, messages: [...(session.messages || []), assistantMsg] }
              : session
          )
        );

        // For non-stream: align new ASSISTANT message to top, unless user scrolled away
        if (assistantMsgId) {
          if (activeSessionIdRef.current === targetSessionId) {
            if (!userScrolledUpRef.current[targetSessionId]) {
              requestAnimationFrame(() => scrollMessageToTop(assistantMsgId, 'smooth', targetSessionId));
            } else {
              // <<< show the tip if user scrolled away while waiting >>>
              setNewMsgTip(prev => ({ ...prev, [targetSessionId]: assistantMsgId }));
            }
          } else {
            setPendingScrollToLastUser(prev => ({ ...prev, [targetSessionId]: assistantMsgId }));
          }
        }
        setIsSending(false);
      }

      if (activeSessionIdRef.current !== targetSessionId) {
        setUnreadSessions(prev => [...new Set([...prev, targetSessionId])]);
      }

      if (isNewChat) {
        fetch(`${ollamaApiUrl}/generate-title`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: targetSessionId,
            message: userMsg.content,
            model: model
          })
        })
        .then(r => r.json())
        .then(data => {
          setChatSessions(prevSessions =>
            prevSessions.map(session =>
              session.session_id === targetSessionId ? { ...session, name: data.title } : session
            )
          );
        });
      }
    } catch (e) {
      console.error("Failed to send message:", e);
      const errorMsg = { role: 'assistant', content: 'Error: ' + e.message, id: `msg-${Date.now()}-${Math.random()}` };
      setChatSessions(prevSessions =>
        prevSessions.map(session =>
          session.session_id === targetSessionId
            ? { ...session, messages: [...session.messages, errorMsg] }
            : session
        )
      );
      setIsSending(false);
    }
  }

  async function createNewChat() {
    const newSessionId = 'sess-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    const res = await fetch(`${ollamaApiUrl}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: newSessionId })
    });
    const newSession = await res.json();
    const sessionWithMessages = { ...newSession, messages: [] };
    setChatSessions(prevSessions => [sessionWithMessages, ...prevSessions]);
    setActiveSessionId(newSession.session_id);
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
    fetch(`${ollamaApiUrl}/sessions/${sessionId}/rename`, {
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

  function handleDelete(sessionId) {
    fetch(`${ollamaApiUrl}/sessions/${sessionId}`, { method: 'DELETE' })
    .then(() => {
      const newSessions = chatSessions.filter(s => s.session_id !== sessionId);
      setChatSessions(newSessions);
      if (activeSessionId === sessionId) {
        setActiveSessionId(newSessions.length > 0 ? newSessions[0].session_id : null);
      }
    });
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
  }, [activeSessionId, chatSessions, ollamaApiUrl]);

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
              <div className="empty-list-message">No databases yet.</div>
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
            </div>
          )}
        </div>
        {activeSidebarMode !== 'settings' && (
          <div className="sidebar-footer">
            {activeSidebarMode === 'chats' && (
              <button className="button new-chat-button" onClick={createNewChat}>New Chat</button>
            )}
            {activeSidebarMode === 'dbs' && (
              <button className="button new-db-button" onClick={() => {}}>New Database</button>
            )}
          </div>
        )}
        <div className="resizer" onMouseDown={startResizing}></div>
      </div>
      <div className="main-content">
        {activeSidebarMode === 'chats' && (
          <>
            <div className="header">
              <strong>Chat - {chatSessions.find(s => s.session_id === activeSessionId)?.name || 'New Chat'}</strong>
            </div>

            <div key={activeSessionId} className="chat" ref={chatRef}>
              {messages.map((m, i) => (
                <div key={m.id || i} id={m.id} className={'msg ' + (m.role === 'user' ? 'user' : 'assistant')}>
                  <div dangerouslySetInnerHTML={{ __html: markdownToHTML(m.content) }} />
                </div>
              ))}
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
                <button className="button" onClick={sendMessage} disabled={isSending}>
                  {isSending ? <div className="spinner"></div> : 'Send'}
                </button>
              </div>
            </div>
          </>
        )}
        {activeSidebarMode === 'dbs' && (
          <div className="placeholder-view">
            <h1>Databases</h1>
            <p>This is a placeholder for the database management view.</p>
          </div>
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
              />
            )}
            {activeSettingsSubmenu === 'Interface' && <InterfaceSettings />}
          </>
        )}
      </div>
    </div>
  )
}
