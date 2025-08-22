import React, { useEffect, useMemo, useRef, useState } from 'react'
import TextareaAutosize from 'react-textarea-autosize';
import GeneralSettings from './GeneralSettings'
import InterfaceSettings from './InterfaceSettings'
import { markdownToHTML } from './markdown';

const API_URL_KEY = 'ollamaApiUrl';
const COLOR_SCHEME_KEY = 'colorScheme';

// Initial API value will be set by useEffect after settings are loaded
let API = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000';

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
  const [loading, setLoading] = useState(true); // Loading state for initial session fetch
  const [unreadSessions, setUnreadSessions] = useState([]); // Track unread messages
  const activeRequestSessionId = useRef(null); // Ref to track the session ID of the active request
  const activeSessionIdRef = useRef(activeSessionId);
  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  // Sidebar resizing state
  const [sidebarWidth, setSidebarWidth] = useState(280); // Initial sidebar width
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

  const messages = useMemo(() => {
    return chatSessions.find(s => s.session_id === activeSessionId)?.messages || [];
  }, [activeSessionId, chatSessions]);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  async function sendMessage() {
    let targetSessionId = activeSessionId;
    let isNewChat = false;

    if (!input.trim() || !model) return;

    if (!targetSessionId) {
      const newSession = await createNewChat();
      targetSessionId = newSession.session_id;
      isNewChat = true;
    } else {
      // Check if it's an existing "New Chat" receiving its first message
      const currentSession = chatSessions.find(s => s.session_id === targetSessionId);
      if (currentSession && currentSession.name === "New Chat" && currentSession.messages.length === 0) {
        isNewChat = true;
      }
    }
    const currentActiveSessionAtSend = activeSessionId; // Capture activeSessionId at send time

    const userMsg = { role: 'user', content: input.trim() };
    
    // Optimistically add user message
    setChatSessions(prevSessions =>
      prevSessions.map(session =>
        session.session_id === targetSessionId
          ? { ...session, messages: [...(session.messages || []), userMsg] }
          : session
      )
    );
    setInput('');

    try {
      const res = await fetch(`${ollamaApiUrl}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: targetSessionId,
          model,
          message: userMsg.content
        })
      });
      const data = await res.json();
      const assistantMsg = { role: 'assistant', content: data.reply };

      // Update messages with assistant's reply
      setChatSessions(prevSessions =>
        prevSessions.map(session =>
          session.session_id === targetSessionId
            ? { ...session, messages: [...(session.messages || []), assistantMsg] }
            : session
        )
      );

      // Handle unread status: only set if the chat was NOT active when the message was sent
      if (activeSessionIdRef.current !== targetSessionId) {
        setUnreadSessions(prev => [...new Set([...prev, targetSessionId])]);
      }

      // Generate title if new chat
      if (isNewChat) {
        fetch(`${ollamaApiUrl}/generate-title`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: targetSessionId,
            message: userMsg.content
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
      // Add error message to chat
      const errorMsg = { role: 'assistant', content: 'Error: ' + e.message };
      setChatSessions(prevSessions =>
        prevSessions.map(session =>
          session.session_id === targetSessionId
            ? { ...session, messages: [...(session.messages || []), errorMsg] }
            : session
        )
      );
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
    setUnreadSessions(prev => prev.filter(id => id !== sessionId));
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
            onClick={() => setActiveSidebarMode('chats')}
          >
            Chats
          </div>
          <div
            className={`sidebar-tab ${activeSidebarMode === 'dbs' ? 'active' : ''}`}
            onClick={() => setActiveSidebarMode('dbs')}
          >
            DBs
          </div>
          <div
            className={`sidebar-tab ${activeSidebarMode === 'settings' ? 'active' : ''}`}
            onClick={() => setActiveSidebarMode('settings')}
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
                  onClick={() => selectChat(session.session_id)}
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

            <div className="chat" ref={chatRef}>
              {messages.map((m, i) => (
                <div key={i} className={'msg ' + (m.role === 'user' ? 'user' : 'assistant')}>
                  <div dangerouslySetInnerHTML={{ __html: markdownToHTML(m.content) }} />
                </div>
              ))}
            </div>

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
                <button className="button" onClick={sendMessage}>Send</button>
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
            {activeSettingsSubmenu === 'General' && <GeneralSettings />}
            {activeSettingsSubmenu === 'Interface' && <InterfaceSettings />}
          </>
        )}
      </div>
    </div>
  )
}
