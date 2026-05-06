// /Users/giers/Heimgeist/src/App.jsx
import React, { useEffect, useMemo, useRef, useState } from 'react'
import TextareaAutosize from 'react-textarea-autosize';
import AssistantMessageContent from './AssistantMessageContent'
import ChatDatabasePicker from './ChatDatabasePicker'
import LibraryManager from './LibraryManager'
import { SettingsPanel, SettingsSidebar } from './SettingsPanels'
import { applyColorScheme } from './colorSchemes'
import {
  AttachmentStrip,
  CHAT_FILE_PICKER_FILTERS,
  attachmentIsImage,
  buildComposerFileAttachment,
  getAttachmentDisplayName,
  getFileName,
  guessMimeTypeFromName,
  hasFilePayload,
  isImageFile,
  isSupportedChatFile,
  isSupportedChatFilePath,
  readFileAsDataUrl,
} from './attachments'
import {
  DEFAULT_BACKEND_API_URL,
  MAX_IMAGE_ATTACHMENT_BYTES,
  MAX_IMAGE_ATTACHMENTS,
  WEBSEARCH_ENGINES_KEY,
  WEBSEARCH_URL_KEY,
  migrateLegacySearxUrl,
  resolveBackendApiUrl,
} from './appConfig'
import {
  expectBackendJson,
  getErrorText,
  isAbortError,
  readBackendErrorText,
} from './backendApi'
import { sanitizeChatTitle, splitThinkBlocks } from './chatText'
import { buildModelPickerOptions } from './modelPicker'
import {
  fetchLocalLibraryContext,
  fetchModelCapabilities,
  fetchStartupOllamaStatus,
  prepareStartupModels,
} from './chatApi'
import { createChatGenerationHandlers } from './chatGeneration'
import {
  loadStoredWebsearchEngines,
  normalizeWebsearchEngines,
} from './websearchEngines'
import { formatRecordingDuration, useAudioInput } from './useAudioInput'
import { useChatLibrarySelection } from './useChatLibrarySelection'
import { useChatScroll } from './useChatScroll'

export default function App() {
  const [chatSessions, setChatSessions] = useState([])
  const [activeSessionId, setActiveSessionId] = useState(null)
  const [activeSidebarMode, setActiveSidebarMode] = useState('chats') // 'chats', 'dbs', 'settings'
  const activeSidebarModeRef = useRef(activeSidebarMode)
  const [activeSettingsSubmenu, setActiveSettingsSubmenu] = useState('General');
  const [editingSessionId, setEditingSessionId] = useState(null); // ID of the session being edited
  const [editingLibrarySlug, setEditingLibrarySlug] = useState(null)
  const [libraries, setLibraries] = useState([])
  const [libraryJobs, setLibraryJobs] = useState([])
  const [activeLibrarySlug, setActiveLibrarySlug] = useState(null)
  const [isCreatingLibrary, setIsCreatingLibrary] = useState(false)
  const [newLibraryName, setNewLibraryName] = useState('')
  const [libraryCreateError, setLibraryCreateError] = useState('')
  const {
    chatLibrary,
    chatLibrarySlug,
    chatLibraryStatusSuffix,
    clearChatLibrarySelections,
    getChatLibraryForSession,
    isLibrarySyncing,
    removeLibraryFromChatSelections,
    setChatLibraryForSession,
  } = useChatLibrarySelection({ activeSessionId, libraries, libraryJobs })
  const [isChatModelPickerOpen, setIsChatModelPickerOpen] = useState(false)
  const [availableChatModels, setAvailableChatModels] = useState([])
  const [availableVisionModels, setAvailableVisionModels] = useState([])
  const [isLoadingModelCatalog, setIsLoadingModelCatalog] = useState(false)

  // Use currentSessionId for the actual chat operations
  const [model, setModel] = useState('')
  const [visionModel, setVisionModel] = useState('')
  const [transcriptionModel, setTranscriptionModel] = useState('base')
  const [selectedChatModelSupportsVision, setSelectedChatModelSupportsVision] = useState(false)
  const [selectedVisionModelSupportsVision, setSelectedVisionModelSupportsVision] = useState(false)
  const [input, setInput] = useState('')
  const [composerAttachments, setComposerAttachments] = useState([])
  const [isAttachmentMenuOpen, setIsAttachmentMenuOpen] = useState(false)
  const [isChatDragActive, setIsChatDragActive] = useState(false)
  const chatRef = useRef(null)
  const textareaRef = useRef(null); // Ref for the textarea
  const modelRef = useRef(model)
  const chatModelPickerRef = useRef(null)
  const attachmentMenuRef = useRef(null)
  const imageInputRef = useRef(null)
  const imageDragDepthRef = useRef(0)
  const [audioInputEnabled, setAudioInputEnabled] = useState(true)
  const [audioInputDeviceId, setAudioInputDeviceId] = useState('')
  const [audioInputLanguage, setAudioInputLanguage] = useState('')
  const [backendApiUrl, setBackendApiUrl] = useState(DEFAULT_BACKEND_API_URL); // State for Heimgeist backend URL
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
  const {
    audioInputRuntimeMessage,
    audioInputRuntimeReady,
    audioRecordingMs,
    isRecordingAudio,
    isTranscribingAudio,
    setAudioInputRuntimeMessage,
    setAudioInputRuntimeReady,
    syncAudioInputRuntimeFromStartupStatus,
    toggleAudioRecording,
  } = useAudioInput({
    activeSidebarMode,
    audioInputDeviceId,
    audioInputEnabled,
    audioInputLanguage,
    backendApiUrl,
    isSending,
    setInput,
    textareaRef,
    transcriptionModel,
  })
  const [loading, setLoading] = useState(true); // Loading state for initial session fetch
  const [unreadSessions, setUnreadSessions] = useState([]); // Track unread messages
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const canAttachImages = selectedChatModelSupportsVision || selectedVisionModelSupportsVision
  const imageAttachmentUnavailableReason = 'Image attachments require a vision-capable chat model or configured vision model.'
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

  async function syncVisionModelFromChatModel(nextModel, options = {}) {
    const { allowCapabilityLookup = true } = options
    if (!nextModel) {
      return false
    }

    if (availableVisionModels.includes(nextModel)) {
      setVisionModel(nextModel)
      window.electronAPI.setSetting('visionModel', nextModel)
      return true
    }

    if (!allowCapabilityLookup || !backendApiUrl) {
      return false
    }

    try {
      const data = await fetchModelCapabilities(backendApiUrl, nextModel)
      if (!data?.supports_vision || modelRef.current !== nextModel) {
        return false
      }
      setVisionModel(nextModel)
      window.electronAPI.setSetting('visionModel', nextModel)
      return true
    } catch (error) {
      if (!isAbortError(error)) {
        console.warn('Failed to check chat model vision capabilities', error)
      }
      return false
    }
  }

  async function handleChatModelSelect(nextModel) {
    if (!nextModel || nextModel === model) {
      setIsChatModelPickerOpen(false)
      return
    }

    setIsChatModelPickerOpen(false)
    setModel(nextModel)
    window.electronAPI.setSetting('chatModel', nextModel)
    await syncVisionModelFromChatModel(nextModel)
  }

  function appendComposerFileAttachments(attachments) {
    if (!Array.isArray(attachments) || attachments.length === 0) {
      return
    }
    setComposerAttachments(prev => [...prev, ...attachments])
  }

  async function appendComposerFilePaths(paths) {
    const nextAttachments = []
    const rejected = []

    for (const rawPath of Array.from(paths || [])) {
      const sourcePath = String(rawPath || '').trim()
      const label = getFileName(sourcePath, 'file')
      if (!sourcePath) {
        rejected.push('One selected file had no usable local path.')
        continue
      }
      if (!isSupportedChatFilePath(sourcePath)) {
        rejected.push(`${label}: unsupported file type for chat attachments.`)
        continue
      }

      nextAttachments.push(buildComposerFileAttachment({
        sourcePath,
        name: getFileName(sourcePath, 'file'),
        mimeType: guessMimeTypeFromName(sourcePath),
      }))
    }

    if (nextAttachments.length > 0) {
      appendComposerFileAttachments(nextAttachments)
    }

    if (rejected.length > 0) {
      window.alert(rejected.join('\n'))
    }
  }

  async function appendComposerImageFiles(fileList) {
    const incoming = Array.from(fileList || []).filter(isImageFile)
    if (!incoming.length) {
      return
    }
    if (!canAttachImages) {
      window.alert(imageAttachmentUnavailableReason)
      return
    }

    const currentImageCount = composerAttachments.filter(attachmentIsImage).length
    const remainingSlots = Math.max(0, MAX_IMAGE_ATTACHMENTS - currentImageCount)
    if (remainingSlots <= 0) {
      window.alert(`You can attach up to ${MAX_IMAGE_ATTACHMENTS} images per message.`)
      return
    }

    const candidates = incoming.slice(0, remainingSlots)
    const oversized = candidates.filter(file => Number(file.size) > MAX_IMAGE_ATTACHMENT_BYTES)
    const acceptedFiles = candidates.filter(file => Number(file.size) <= MAX_IMAGE_ATTACHMENT_BYTES)

    if (oversized.length > 0) {
      window.alert(`Images must be ${Math.round(MAX_IMAGE_ATTACHMENT_BYTES / (1024 * 1024))} MB or smaller.`)
    }

    if (!acceptedFiles.length) {
      return
    }

    try {
      const nextAttachments = await Promise.all(
        acceptedFiles.map(async (file, index) => ({
          id: `attachment-${Date.now()}-${index}-${Math.random().toString(36).slice(2)}`,
          name: file.name || 'image',
          mime_type: file.type || 'image/*',
          data_url: await readFileAsDataUrl(file),
        }))
      )

      setComposerAttachments(prev => [...prev, ...nextAttachments])

      if (incoming.length > remainingSlots) {
        window.alert(`Only the first ${MAX_IMAGE_ATTACHMENTS} images can be attached.`)
      }
    } catch (error) {
      console.error('Failed to load image attachments', error)
      window.alert(`Image import failed: ${getErrorText(error)}`)
    }
  }

  function removeComposerAttachment(attachmentId) {
    setComposerAttachments(prev => prev.filter(attachment => attachment.id !== attachmentId))
  }

  function openImagePicker() {
    if (!canAttachImages) {
      return
    }
    imageInputRef.current?.click()
  }

  async function openFilePicker() {
    try {
      const pickedPaths = await window.electronAPI?.pickPaths?.({
        title: 'Select files for chat',
        filters: CHAT_FILE_PICKER_FILTERS,
      })
      await appendComposerFilePaths(pickedPaths)
    } catch (error) {
      console.error('Failed to open file picker', error)
      window.alert(`File selection failed: ${getErrorText(error)}`)
    }
  }

  async function appendDroppedChatFiles(fileList) {
    const incoming = Array.from(fileList || [])
    if (!incoming.length) {
      return
    }

    const imageFiles = []
    const fileAttachments = []
    const rejected = []

    for (const file of incoming) {
      if (isImageFile(file)) {
        if (!canAttachImages) {
          rejected.push(`${file.name || 'image'}: ${imageAttachmentUnavailableReason}`)
          continue
        }
        imageFiles.push(file)
        continue
      }

      if (!isSupportedChatFile(file)) {
        rejected.push(`${file?.name || 'file'}: unsupported file type for chat attachments.`)
        continue
      }

      const sourcePath = String(file?.path || '').trim()
      if (!sourcePath) {
        rejected.push(`${file.name || 'file'}: local file paths are required for drag and drop in the desktop app.`)
        continue
      }

      fileAttachments.push(buildComposerFileAttachment({
        sourcePath,
        name: file.name || getFileName(sourcePath, 'file'),
        mimeType: file.type || guessMimeTypeFromName(file.name || sourcePath),
        size: file.size,
      }))
    }

    if (imageFiles.length > 0) {
      await appendComposerImageFiles(imageFiles)
    }
    if (fileAttachments.length > 0) {
      appendComposerFileAttachments(fileAttachments)
    }
    if (rejected.length > 0) {
      window.alert(rejected.join('\n'))
    }
  }

  async function handleComposerImageSelection(event) {
    const files = event.target?.files
    try {
      await appendComposerImageFiles(files)
    } finally {
      if (event.target) {
        event.target.value = ''
      }
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

  const conversationNeedsVision = msgs
    .slice(0, lastUserIdx + 1)
    .some(messageHasImageAttachments)
  if (conversationNeedsVision && !canAttachImages) {
    window.alert(imageAttachmentUnavailableReason)
    return
  }

  const requestController = beginCancelableRequest(sessionId)

  let enrichedPrompt = overrideUserText != null ? overrideUserText : (msgs[lastUserIdx]?.content || '')
  let citationSources = []
  const contextBlocks = []
  try {
    const selectedLibrary = getChatLibraryForSession(sessionId)
    const promptText = overrideUserText != null ? overrideUserText : (msgs[lastUserIdx]?.content || '')
    const hasPromptText = Boolean((promptText || '').trim())

    if (hasPromptText && selectedLibrary?.states?.is_indexed) {
      try {
        const localContext = await fetchLocalLibraryContext(backendApiUrl, selectedLibrary.slug, promptText, requestController.signal)
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

    if (hasPromptText && webSearchEnabled) {
      try {
        const historyForSearch = msgs
          .slice(Math.max(0, lastUserIdx - 7), lastUserIdx + 1)
          .map(m => ({ role: m.role, content: m.content || '' }))
        if (historyForSearch.length > 0) {
          historyForSearch[historyForSearch.length - 1] = { role: 'user', content: promptText }
        }

        const searchContext = await fetchWebSearchContext({
          apiBase: backendApiUrl,
          engines: searxEngines,
          historyLimit: 8,
          messages: historyForSearch,
          model,
          prompt: promptText,
          searxUrl,
          signal: requestController.signal,
        })
        if (searchContext.contextBlock) {
          contextBlocks.push(searchContext.contextBlock)
        }
        if (Array.isArray(searchContext.sources)) {
          citationSources.push(...searchContext.sources)
        }
      } catch (error) {
        if (isAbortError(error)) throw error
        console.warn('web search enrichment (regenerate) failed', error)
      }
    }

    citationSources = [...new Set(citationSources)]
    if (hasPromptText && contextBlocks.length > 0) {
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
        const res = await postRegenerateMessage({
          apiBase: backendApiUrl,
          enrichedMessage: enrichedPrompt,
          index,
          model,
          sessionId,
          signal: requestController.signal,
          sources: citationSources || [],
          stream: true,
          transcriptionModel,
          visionModel,
        })
        if (!res.ok) throw new Error(await readBackendErrorText(res))

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
      const res = await postRegenerateMessage({
        apiBase: backendApiUrl,
        enrichedMessage: enrichedPrompt,
        index,
        model,
        sessionId,
        signal: requestController.signal,
        sources: citationSources || [],
        stream: false,
        transcriptionModel,
        visionModel,
      })
      if (!res.ok) throw new Error(await readBackendErrorText(res))

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


  // Collapse state per user message: { [msgKey]: boolean } — true means "collapsed"
  const [collapsedUserMsgs, setCollapsedUserMsgs] = useState({});

  useEffect(() => {
    activeSidebarModeRef.current = activeSidebarMode
  }, [activeSidebarMode])

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

  const activeRequestRef = useRef(null);
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
    window.addEventListener('blur', stopResizing);
    return () => {
      window.removeEventListener('mousemove', resizeSidebar);
      window.removeEventListener('mouseup', stopResizing);
      window.removeEventListener('blur', stopResizing);
    };
  }, [resizeSidebar, stopResizing]);

  React.useEffect(() => {
    if (isResizing) {
      document.body.classList.add('no-select');
    } else {
      document.body.classList.remove('no-select');
    }

    return () => {
      document.body.classList.remove('no-select');
    };
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


  useEffect(() => {
    let cancelled = false

    window.electronAPI.getSettings().then(settings => {
      if (cancelled) return
      setBackendApiUrl(resolveBackendApiUrl(settings));
      setColorScheme(settings.colorScheme || 'Default');
      setModel(settings.chatModel || ''); // Load the selected model, with a fallback
      setVisionModel(settings.visionModel || settings.chatModel || '');
      setTranscriptionModel(settings.transcriptionModel || 'base');
      setStreamOutput(settings.streamOutput || false);
      setAudioInputEnabled(true);
      if (settings.audioInputEnabled !== true) {
        window.electronAPI.setSetting('audioInputEnabled', true)
      }
      setAudioInputDeviceId(typeof settings.audioInputDeviceId === 'string' ? settings.audioInputDeviceId : '');
      setAudioInputLanguage(typeof settings.audioInputLanguage === 'string' ? settings.audioInputLanguage : '');
      setScrollPositions(settings.scrollPositions || {}); // Load scroll positions
      applyColorScheme(settings.colorScheme || 'Default'); // Apply initial scheme
    }).finally(() => {
      if (!cancelled) {
        setSettingsLoaded(true);
      }
    });

    return () => {
      cancelled = true
    };
  }, []);

  useEffect(() => {
    modelRef.current = model
  }, [model])

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()

    if (!backendApiUrl) {
      setAvailableChatModels([])
      setAvailableVisionModels([])
      setIsLoadingModelCatalog(false)
      return () => {
        controller.abort()
      }
    }

    setIsLoadingModelCatalog(true)

    ;(async () => {
      try {
        const response = await fetch(`${backendApiUrl}/models`, { signal: controller.signal })
        const data = await expectBackendJson(response)
        if (cancelled) {
          return
        }
        setAvailableChatModels(Array.isArray(data?.chat_models) ? data.chat_models.filter(Boolean) : [])
        setAvailableVisionModels(Array.isArray(data?.vision_models) ? data.vision_models.filter(Boolean) : [])
      } catch (error) {
        if (!cancelled && !isAbortError(error)) {
          console.warn('Failed to load chat model catalog', error)
        }
      } finally {
        if (!cancelled) {
          setIsLoadingModelCatalog(false)
        }
      }
    })()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [backendApiUrl])

  useEffect(() => {
    const handleFocus = () => {
      if (activeSidebarModeRef.current === 'chats') {
        textareaRef.current?.focus();
      }
    };

    window.electronAPI.onWindowFocus(handleFocus);

    return () => {
      window.electronAPI.offWindowFocus(handleFocus);
    };
  }, []);

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()

    if (!backendApiUrl || !model) {
      setSelectedChatModelSupportsVision(false)
      return () => {
        controller.abort()
      }
    }

    ;(async () => {
      try {
        const data = await fetchModelCapabilities(backendApiUrl, model, controller.signal)
        if (!cancelled) {
          setSelectedChatModelSupportsVision(Boolean(data?.supports_vision))
        }
      } catch (error) {
        if (!cancelled && !isAbortError(error)) {
          console.warn('Failed to load chat model capabilities', error)
          setSelectedChatModelSupportsVision(false)
        }
      }
    })()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [backendApiUrl, model])

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()

    if (!backendApiUrl || !visionModel) {
      setSelectedVisionModelSupportsVision(false)
      return () => {
        controller.abort()
      }
    }

    ;(async () => {
      try {
        const data = await fetchModelCapabilities(backendApiUrl, visionModel, controller.signal)
        if (!cancelled) {
          setSelectedVisionModelSupportsVision(Boolean(data?.supports_vision))
        }
      } catch (error) {
        if (!cancelled && !isAbortError(error)) {
          console.warn('Failed to load model capabilities', error)
          setSelectedVisionModelSupportsVision(false)
        }
      }
    })()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [backendApiUrl, visionModel])

  useEffect(() => {
    imageDragDepthRef.current = 0
    setIsChatDragActive(false)
  }, [canAttachImages, activeSidebarMode])

  useEffect(() => {
    if (!settingsLoaded || loading || !backendApiUrl || startupOllamaCheckRanRef.current) return
    startupOllamaCheckRanRef.current = true

    let cancelled = false
    const timerId = window.setTimeout(() => { ;(async () => {
      let actionStarted = false
      try {
        let status = await fetchStartupOllamaStatus(backendApiUrl)
        if (cancelled) return
        syncAudioInputRuntimeFromStartupStatus(status)

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
          const prepared = await prepareStartupModels(backendApiUrl)
          if (cancelled) return
          syncAudioInputRuntimeFromStartupStatus(prepared?.ollama || status)
        }
      } catch (error) {
        if (!cancelled) {
          console.warn('startup Ollama check failed', error)
          setAudioInputRuntimeReady(false)
          setAudioInputRuntimeMessage(`Whisper availability could not be verified: ${getErrorText(error)}`)
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
    clearChatLibrarySelections()
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
        const sessionsWithMessages = data.sessions.map(s => ({
          ...s,
          name: sanitizeChatTitle(s.name),
          messages: [],
        }));
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

  const {
    activeSessionIdRef,
    handleNewMsgTipClick,
    newMsgTip,
    restoredForRef,
    scrollMessageToTop,
    scrollPendingMessageForSession,
    scrollToBottom,
    setNewMsgTip,
    setPendingScrollToLastUser,
    setScrollPositions,
    setUserScrolledUp,
    userScrolledUpRef,
  } = useChatScroll({
    activeSessionId,
    activeSidebarMode,
    chatRef,
    messagesLength: messages.length,
  })

  const activeChatSession = useMemo(() => {
    return chatSessions.find(session => session.session_id === activeSessionId) || null
  }, [activeSessionId, chatSessions])

  const activeLibrary = useMemo(() => {
    return libraries.find(lib => lib.slug === activeLibrarySlug) || null;
  }, [activeLibrarySlug, libraries]);

  const chatModelPickerOptions = useMemo(() => {
    return buildModelPickerOptions(availableChatModels, model, 'saved model unavailable')
  }, [availableChatModels, model])

  useEffect(() => {
    if (!isChatModelPickerOpen) return

    const onPointerDown = (event) => {
      if (!chatModelPickerRef.current?.contains(event.target)) {
        setIsChatModelPickerOpen(false)
      }
    }

    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [isChatModelPickerOpen])

  useEffect(() => {
    setIsChatModelPickerOpen(false)
  }, [activeSessionId, activeSidebarMode])

  useEffect(() => {
    if (!isAttachmentMenuOpen) return

    const onPointerDown = (event) => {
      if (!attachmentMenuRef.current?.contains(event.target)) {
        setIsAttachmentMenuOpen(false)
      }
    }

    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [isAttachmentMenuOpen])

  useEffect(() => {
    setIsAttachmentMenuOpen(false)
  }, [activeSessionId, activeSidebarMode, canAttachImages])

  const handleChatDragEnter = (event) => {
    if (activeSidebarMode !== 'chats' || !hasFilePayload(event)) return
    event.preventDefault()
    imageDragDepthRef.current += 1
    setIsChatDragActive(true)
  }

  const handleChatDragOver = (event) => {
    if (activeSidebarMode !== 'chats' || !hasFilePayload(event)) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
    if (!isChatDragActive) {
      setIsChatDragActive(true)
    }
  }

  const handleChatDragLeave = (event) => {
    if (activeSidebarMode !== 'chats' || !hasFilePayload(event)) return
    imageDragDepthRef.current = Math.max(0, imageDragDepthRef.current - 1)
    if (imageDragDepthRef.current === 0) {
      setIsChatDragActive(false)
    }
  }

  const handleChatDrop = async (event) => {
    if (activeSidebarMode !== 'chats' || !hasFilePayload(event)) return
    event.preventDefault()
    imageDragDepthRef.current = 0
    setIsChatDragActive(false)
    await appendDroppedChatFiles(event.dataTransfer?.files)
    textareaRef.current?.focus()
  }


async function sendMessage() {
  const trimmedInput = input.trim()
  if (isSending || (!trimmedInput && composerAttachments.length === 0) || !model) return
  if (composerAttachments.some(attachmentIsImage) && !canAttachImages) {
    window.alert(imageAttachmentUnavailableReason)
    return
  }

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

  const existingMessages = (chatSessions.find(s => s.session_id === targetSessionId)?.messages) || []
  const outgoingAttachments = composerAttachments.map(({ id, ...attachment }) => ({ ...attachment }))
  const historyNeedsVision = existingMessages.some(messageHasImageAttachments)
  if (historyNeedsVision && !canAttachImages) {
    window.alert(imageAttachmentUnavailableReason)
    return
  }
  const composerSnapshot = input
  const attachmentSnapshot = composerAttachments.map(attachment => ({ ...attachment }))
  const userMsg = {
    role: 'user',
    content: trimmedInput,
    attachments: outgoingAttachments,
    id: `msg-${Date.now()}-${Math.random()}`
  }
  setIsAttachmentMenuOpen(false)
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
    setComposerAttachments([])
  })
  requestAnimationFrame(() => scrollToBottom('auto', targetSessionId))

  const requestController = beginCancelableRequest(targetSessionId)
  try {
    let historyForSearch = []
    if (userMsg.content) try {
      const existing = (chatSessions.find(s => s.session_id === targetSessionId)?.messages) || []
      const lastFew = existing.slice(-8).map(m => ({ role: m.role, content: m.content || '' }))
      historyForSearch = [...lastFew, { role: 'user', content: userMsg.content }]
    } catch {}

    let enrichedPrompt = userMsg.content || null
    let citationSources = []
    const contextBlocks = []

    const selectedLibrary = getChatLibraryForSession(targetSessionId)

    if (userMsg.content && selectedLibrary?.states?.is_indexed) {
      try {
        const localContext = await fetchLocalLibraryContext(backendApiUrl, selectedLibrary.slug, userMsg.content, requestController.signal)
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

    if (userMsg.content && webSearchEnabled) {
      try {
        const searchContext = await fetchWebSearchContext({
          apiBase: backendApiUrl,
          engines: searxEngines,
          historyLimit: 8,
          messages: historyForSearch,
          model,
          prompt: userMsg.content,
          searxUrl,
          signal: requestController.signal,
        })
        if (searchContext.contextBlock) {
          contextBlocks.push(searchContext.contextBlock)
        }
        if (Array.isArray(searchContext.sources)) {
          citationSources.push(...searchContext.sources)
        }
      } catch (error) {
        if (isAbortError(error)) throw error
        console.warn('web search enrichment failed', error)
      }
    }

    citationSources = [...new Set(citationSources)]
    if (userMsg.content && contextBlocks.length > 0) {
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
        const res = await postChatMessage({
          apiBase: backendApiUrl,
          attachments: outgoingAttachments,
          enrichedMessage: userMsg.content && contextBlocks.length > 0 ? enrichedPrompt : null,
          message: userMsg.content,
          model,
          sessionId: targetSessionId,
          signal: requestController.signal,
          sources: citationSources || [],
          stream: true,
          transcriptionModel,
          visionModel,
        })
        if (!res.ok) {
          const error = new Error(await readBackendErrorText(res))
          error.status = res.status
          throw error
        }

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
        if (Number(error?.status) >= 400 && Number(error?.status) < 500) {
          setAssistantMessageContent(targetSessionId, assistantMsgId, fullReply, { removeIfEmpty: true })
          setChatSessions(prevSessions =>
            prevSessions.map(session =>
              session.session_id === targetSessionId
                ? {
                    ...session,
                    messages: (session.messages || []).filter(message => message.id !== userMsg.id),
                  }
                : session
            )
          )
          setInput(composerSnapshot)
          setComposerAttachments(attachmentSnapshot)
          window.alert(getErrorText(error))
          return
        }
        setAssistantMessageContent(targetSessionId, assistantMsgId, 'Error: ' + getErrorText(error), { removeIfEmpty: true })
        return
      }
    } else {
      const res = await postChatMessage({
        apiBase: backendApiUrl,
        attachments: outgoingAttachments,
        enrichedMessage: userMsg.content && contextBlocks.length > 0 ? enrichedPrompt : null,
        message: userMsg.content,
        model,
        sessionId: targetSessionId,
        signal: requestController.signal,
        sources: citationSources || [],
        stream: false,
        transcriptionModel,
        visionModel,
      })
      if (!res.ok) {
        const error = new Error(await readBackendErrorText(res))
        error.status = res.status
        throw error
      }

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
      requestGeneratedTitle({
        apiBase: backendApiUrl,
        message: buildAttachmentTitleSeed(userMsg.content, outgoingAttachments),
        model,
        sessionId: targetSessionId,
      })
      .then(r => r.json())
      .then(data => {
        const sanitizedTitle = sanitizeChatTitle(data.title)
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
    if (Number(error?.status) >= 400 && Number(error?.status) < 500) {
      setChatSessions(prevSessions =>
        prevSessions.map(session =>
          session.session_id === targetSessionId
            ? {
                ...session,
                messages: (session.messages || []).filter(message => message.id !== userMsg.id),
              }
            : session
        )
      )
      setInput(composerSnapshot)
      setComposerAttachments(attachmentSnapshot)
      window.alert(getErrorText(error))
      return
    }
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
    const sessionWithMessages = { ...newSession, name: sanitizeChatTitle(newSession.name), messages: [] };
    setChatSessions(prevSessions => [sessionWithMessages, ...prevSessions]);
    setActiveSessionId(newSession.session_id);
    textareaRef.current?.focus();
    return newSession;
  }

  function selectChat(sessionId) {
    setActiveSessionId(sessionId);
    // Clear unread dot immediately for this chat
    setUnreadSessions(prev => prev.filter(id => id !== sessionId));
    scrollPendingMessageForSession(sessionId, chatSessions)
  }

  function handleRename(sessionId, newName) {
    const sanitizedName = sanitizeChatTitle(newName)
    fetch(`${backendApiUrl}/sessions/${sessionId}/rename`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: sanitizedName })
    })
    .then(() => {
      setChatSessions(prevSessions =>
        prevSessions.map(session =>
          session.session_id === sessionId ? { ...session, name: sanitizedName } : session
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
      setChatLibraryForSession(sessionId, null)
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
            <SettingsSidebar
              activeSection={activeSettingsSubmenu}
              onSelect={setActiveSettingsSubmenu}
            />
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
      <div
        className={`main-content${activeSidebarMode === 'chats' && isChatDragActive ? ' main-content--drag-active' : ''}`}
        onDragEnter={handleChatDragEnter}
        onDragOver={handleChatDragOver}
        onDragLeave={handleChatDragLeave}
        onDrop={handleChatDrop}
      >
        {startupTaskMessage && (
          <div className="startup-task-banner" role="status" aria-live="polite">
            {startupTaskBusy && <div className="spinner startup-task-banner__spinner"></div>}
            <div className="startup-task-banner__text">{startupTaskMessage}</div>
          </div>
        )}
        {activeSidebarMode === 'chats' && (
          <>
            <div className="header header--chat">
              <div className="header-main">
                <strong className="header-title">
                  Chat - {activeChatSession?.name || 'New Chat'}
                </strong>
                {chatLibrary && (
                  <span className="header-subtle">
                    {`DB: ${chatLibrary.name}${chatLibraryStatusSuffix}`}
                  </span>
                )}
              </div>
              <div className="header-actions">
                <div className="model-picker" ref={chatModelPickerRef}>
                  <button
                    type="button"
                    className="model-picker-toggle"
                    onClick={() => setIsChatModelPickerOpen(prev => !prev)}
                    aria-haspopup="menu"
                    aria-expanded={isChatModelPickerOpen}
                    title={model ? `Current chat model: ${model}` : 'Select chat model'}
                    disabled={!model && chatModelPickerOptions.length === 0}
                  >
                    <span className="model-picker-label">
                      {model || (isLoadingModelCatalog ? 'Loading models…' : 'Select model')}
                    </span>
                    <span className="model-picker-caret" aria-hidden="true">
                      {isChatModelPickerOpen ? '▴' : '▾'}
                    </span>
                  </button>
                  {isChatModelPickerOpen && (
                    <div className="model-picker-menu" role="menu">
                      {chatModelPickerOptions.length === 0 ? (
                        <div className="model-picker-empty">
                          {isLoadingModelCatalog ? 'Loading models…' : 'No chat models available.'}
                        </div>
                      ) : (
                        chatModelPickerOptions.map(option => {
                          const selected = option.value === model
                          return (
                            <button
                              key={option.value}
                              type="button"
                              className={`model-picker-option${selected ? ' selected' : ''}`}
                              onClick={() => handleChatModelSelect(option.value)}
                            >
                              <span className="model-picker-option-label">{option.label}</span>
                              {selected && <span className="model-picker-status">Selected</span>}
                            </button>
                          )
                        })
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div
              key={activeSessionId}
              className={`chat${isChatDragActive ? ' chat--drag-active' : ''}`}
              ref={chatRef}
              onClick={handleChatFrameClick}
            >
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
                          <>
                            <AttachmentStrip attachments={m.attachments} className="message-attachment-strip" />
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
                          </>
                        ) : (
                          (() => {
                            const raw = m.content || '';
                            const attachments = Array.isArray(m.attachments) ? m.attachments : [];
                            const lines = raw.split(/\r\n|\r|\n/);
                            const needsCollapse = lines.length > 30;
                            const key = collapseKeyFor(m, i, activeSessionId);
                            const isCollapsed = needsCollapse ? (collapsedUserMsgs[key] ?? true) : false;
                            const displayText = isCollapsed ? lines.slice(0, 30).join('\n') + '\n…' : raw;
                            const hasText = Boolean(raw.trim());

                            return (
                              <>
                                <AttachmentStrip attachments={attachments} className="message-attachment-strip" />
                                {hasText && <div className="msg-content msg-content--user">{displayText}</div>}
                                {hasText && needsCollapse && (
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
              <div className={`footer-inner${isChatDragActive ? ' footer-inner--drag-active' : ''}`}>
                <input
                  ref={imageInputRef}
                  type="file"
                  accept="image/*"
                  multiple
                  className="composer-image-input"
                  onChange={handleComposerImageSelection}
                  tabIndex={-1}
                />
                <AttachmentStrip
                  attachments={composerAttachments}
                  className="composer-attachment-strip"
                  removable
                  onRemove={removeComposerAttachment}
                />
                {(isRecordingAudio || isTranscribingAudio) && (
                  <div
                    className={
                      'composer-audio-status' +
                      (isRecordingAudio ? ' composer-audio-status--recording' : ' composer-audio-status--transcribing')
                    }
                    role="status"
                    aria-live="polite"
                  >
                    {isRecordingAudio ? (
                      <span className="composer-audio-status__dot" aria-hidden="true"></span>
                    ) : (
                      <div className="spinner composer-audio-status__spinner" aria-hidden="true"></div>
                    )}
                    <span>
                      {isRecordingAudio
                        ? `Listening ${formatRecordingDuration(audioRecordingMs)}`
                        : 'Transcribing audio…'}
                    </span>
                  </div>
                )}
                <div className="footer-content-wrapper">
                <TextareaAutosize
                  ref={textareaRef}
                  className="input"
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey && !isRecordingAudio && !isTranscribingAudio) {
                      e.preventDefault();
                      sendMessage();
                    }
                  }}
                  placeholder="Ask any question..."
                  maxRows={13}
                />
                <ChatDatabasePicker
                  activeSessionId={activeSessionId}
                  chatLibrary={chatLibrary}
                  chatLibrarySlug={chatLibrarySlug}
                  chatLibraryStatusSuffix={chatLibraryStatusSuffix}
                  isLibrarySyncing={isLibrarySyncing}
                  libraries={libraries}
                  setChatLibraryForSession={setChatLibraryForSession}
                />
                  <div className="footer-tool-group" ref={attachmentMenuRef}>
                    <button
                      type="button"
                      className={"attachment-toggle" + (composerAttachments.length > 0 ? " active" : "")}
                      onClick={() => setIsAttachmentMenuOpen(prev => !prev)}
                      title="Add attachments"
                      aria-label="Add attachments"
                      aria-haspopup="menu"
                      aria-expanded={isAttachmentMenuOpen}
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
                           stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                           aria-hidden="true">
                        <path d="M12 5v14"/>
                        <path d="M5 12h14"/>
                      </svg>
                    </button>
                    {isAttachmentMenuOpen && (
                      <div className="attachment-menu" role="menu">
                        <button
                          type="button"
                          className="attachment-menu-option"
                          onClick={() => {
                            setIsAttachmentMenuOpen(false)
                            openImagePicker()
                          }}
                          disabled={!canAttachImages}
                          title={canAttachImages ? 'Add one or more image files' : imageAttachmentUnavailableReason}
                        >
                          <span>Add Image(s)</span>
                          {!canAttachImages && <span className="attachment-menu-status">Unavailable</span>}
                        </button>
                        <button
                          type="button"
                          className="attachment-menu-option"
                          onClick={async () => {
                            setIsAttachmentMenuOpen(false)
                            await openFilePicker()
                          }}
                          title="Add supported document, text, audio, or video files"
                        >
                          <span>Add File(s)</span>
                        </button>
                        {!canAttachImages && (
                          <div className="attachment-menu-hint">
                            {imageAttachmentUnavailableReason}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                  {audioInputEnabled && (
                    <button
                      type="button"
                      className={
                        'audio-input-toggle' +
                        (isRecordingAudio || isTranscribingAudio ? ' active' : '') +
                        (isRecordingAudio ? ' recording' : '') +
                        (isTranscribingAudio ? ' transcribing' : '')
                      }
                      onClick={toggleAudioRecording}
                      title={
                        !audioInputRuntimeReady
                          ? (audioInputRuntimeMessage || 'Whisper is not available for audio input.')
                          : isRecordingAudio
                          ? 'Stop voice input'
                          : (isTranscribingAudio ? 'Transcribing audio' : 'Start voice input')
                      }
                      aria-label={
                        !audioInputRuntimeReady
                          ? (audioInputRuntimeMessage || 'Whisper is not available for audio input.')
                          : isRecordingAudio
                          ? 'Stop voice input'
                          : (isTranscribingAudio ? 'Transcribing audio' : 'Start voice input')
                      }
                      aria-pressed={isRecordingAudio}
                      disabled={!audioInputRuntimeReady || isTranscribingAudio || isSending}
                    >
                      {isTranscribingAudio ? (
                        <div className="spinner composer-audio-icon-spinner" aria-hidden="true"></div>
                      ) : (
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
                             stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                             aria-hidden="true">
                          <path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3z"/>
                          <path d="M19 10a7 7 0 0 1-14 0"/>
                          <line x1="12" y1="19" x2="12" y2="22"/>
                          <line x1="8" y1="22" x2="16" y2="22"/>
                        </svg>
                      )}
                    </button>
                  )}
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
                  disabled={!isSending && (isRecordingAudio || isTranscribingAudio)}
                >
                  {isSending ? <div className="spinner"></div> : 'Send'}
                </button>
                </div>
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
          <SettingsPanel
            activeSection={activeSettingsSubmenu}
            onAudioInputDeviceChange={setAudioInputDeviceId}
            onAudioInputLanguageChange={setAudioInputLanguage}
            onBackendApiUrlChange={setBackendApiUrl}
            onLibrariesPurged={handleLibrariesPurged}
            onModelChange={setModel}
            onStreamOutputChange={setStreamOutput}
            onTranscriptionModelChange={setTranscriptionModel}
            onVisionModelChange={setVisionModel}
            searxEngines={searxEngines}
            searxUrl={searxUrl}
            setSearxEngines={setSearxEngines}
            setSearxUrl={setSearxUrl}
            streamOutput={streamOutput}
          />
        )}
      </div>
    </div>
  )
}
