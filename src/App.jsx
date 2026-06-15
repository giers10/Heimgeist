// /Users/giers/Heimgeist/src/App.jsx
import React, { useEffect, useMemo, useRef, useState } from 'react'
import TextareaAutosize from 'react-textarea-autosize';
import AssistantMessageContent from './AssistantMessageContent'
import ChatDatabasePicker from './ChatDatabasePicker'
import LibraryManager from './LibraryManager'
import SaveMessageToKnowledgeDialog from './SaveMessageToKnowledgeDialog'
import { SettingsPanel, SettingsSidebar } from './SettingsPanels'
import { applyColorScheme } from './colorSchemes'
import {
  AttachmentStrip,
  CHAT_FILE_PICKER_FILTERS,
  attachmentIsImage,
  buildComposerFileAttachment,
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
} from './backendApi'
import { sanitizeChatTitle, splitThinkBlocks } from './chatText'
import { buildModelPickerOptions } from './modelPicker'
import {
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
import { useChatWorkflowSelection } from './useChatWorkflowSelection'
import { useChatScroll } from './useChatScroll'
import desktopApi from './desktop/desktopApi'
import WorkflowsArea from './workflows/WorkflowsArea'
import WorkflowConfirmationDialog from './workflows/WorkflowConfirmationDialog'
import WorkflowExecutionPanel from './workflows/WorkflowExecutionPanel'
import WorkflowList from './workflows/WorkflowList'
import WorkflowSelector from './workflows/WorkflowSelector'
import {
  cancelWorkflowRun,
  createWorkflow,
  deleteWorkflow,
  duplicateWorkflow,
  fetchTools,
  fetchWorkflow,
  fetchWorkflows,
  respondToConfirmation,
} from './workflows/workflowApi'

const DEFAULT_WORKFLOW_GRAPH = {
  schema_version: 1,
  inputs: { prompt: { type: 'string' }, session_id: { type: ['string', 'null'] } },
  outputs: { content: { type: 'string' } },
  nodes: [
    { id: 'input', type: 'input', position: { x: 80, y: 120 }, config: {} },
    { id: 'output', type: 'output', position: { x: 440, y: 120 }, config: { value: { content: { $ref: 'input.prompt' }, sources: [], usage: {} } } },
  ],
  edges: [{ id: 'input-output', source: 'input', source_handle: 'output', target: 'output', target_handle: 'input' }],
  limits: { maximum_nodes: 40, maximum_tool_calls: 20, maximum_llm_calls: 8, maximum_runtime_seconds: 300, maximum_result_chars: 120000, maximum_concurrency: 4 },
}

export default function App() {
  const [chatSessions, setChatSessions] = useState([])
  const [activeSessionId, setActiveSessionId] = useState(null)
  const [activeSidebarMode, setActiveSidebarMode] = useState('chats') // 'chats', 'dbs', 'workflows', 'settings'
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
  const [workflows, setWorkflows] = useState([])
  const [workflowTools, setWorkflowTools] = useState([])
  const [activeWorkflowId, setActiveWorkflowId] = useState(null)
  const [activeWorkflow, setActiveWorkflow] = useState(null)
  const [workflowError, setWorkflowError] = useState('')
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
  const {
    getSelectionForSession: getChatWorkflowForSession,
    selectedWorkflow,
    selection: chatWorkflowSelection,
    setSelectionForSession: setChatWorkflowForSession,
  } = useChatWorkflowSelection({ activeSessionId, workflows })
  const [isChatModelPickerOpen, setIsChatModelPickerOpen] = useState(false)
  const [availableChatModels, setAvailableChatModels] = useState([])
  const [availableVisionModels, setAvailableVisionModels] = useState([])
  const [isLoadingModelCatalog, setIsLoadingModelCatalog] = useState(false)

  // Use currentSessionId for the actual chat operations
  const [model, setModel] = useState('')
  const [visionModel, setVisionModel] = useState('')
  const [transcriptionModel, setTranscriptionModel] = useState('base')
  const [workflowRouterModel, setWorkflowRouterModel] = useState('')
  const [workflowSelectionMode, setWorkflowSelectionMode] = useState('auto')
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
  const [workflowExecutions, setWorkflowExecutions] = useState({})
  const [pendingWorkflowConfirmation, setPendingWorkflowConfirmation] = useState(null)
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
  const [knowledgeSaveTarget, setKnowledgeSaveTarget] = useState(null)
  const [knowledgeSaveToast, setKnowledgeSaveToast] = useState('')
  const knowledgeJumpTargetRef = useRef(null)
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
      desktopApi.setSetting('visionModel', nextModel)
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
      desktopApi.setSetting('visionModel', nextModel)
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
    desktopApi.setSetting('chatModel', nextModel)
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
      const pickedPaths = await desktopApi.pickPaths({
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

  const attachWorkflowRunToRequest = React.useCallback((controller, runId) => {
    if (activeRequestRef.current?.controller !== controller) return
    activeRequestRef.current.runId = runId
  }, [])

  const cancelActiveRequest = React.useCallback(() => {
    const activeRequest = activeRequestRef.current
    if (!activeRequest) return
    activeRequestRef.current = null
    if (activeRequest.runId) {
      cancelWorkflowRun(backendApiUrl, activeRequest.runId).catch(error => {
        console.warn('Workflow cancellation failed', error)
      })
    }
    activeRequest.controller.abort()
    setIsSending(false)
  }, [backendApiUrl])

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

    desktopApi.getSettings().then(settings => {
      if (cancelled) return
      setBackendApiUrl(resolveBackendApiUrl(settings));
      setColorScheme(settings.colorScheme || 'Default');
      setModel(settings.chatModel || ''); // Load the selected model, with a fallback
      setVisionModel(settings.visionModel || settings.chatModel || '');
      setTranscriptionModel(settings.transcriptionModel || 'base');
      setWorkflowRouterModel(settings.workflowRouterModel || '');
      setWorkflowSelectionMode(settings.workflowSelectionMode === 'manual' ? 'manual' : 'auto');
      setStreamOutput(settings.streamOutput || false);
      setAudioInputEnabled(true);
      if (settings.audioInputEnabled !== true) {
        desktopApi.setSetting('audioInputEnabled', true)
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

  async function refreshWorkflows(preferredId = null) {
    if (!backendApiUrl) return
    try {
      const [workflowData, toolData] = await Promise.all([
        fetchWorkflows(backendApiUrl),
        fetchTools(backendApiUrl),
      ])
      const nextWorkflows = Array.isArray(workflowData.workflows) ? workflowData.workflows : []
      const nextTools = Array.isArray(toolData.tools) ? toolData.tools : []
      const fallbackId = activeWorkflowId && nextWorkflows.some(workflow => workflow.id === activeWorkflowId)
        ? activeWorkflowId
        : nextWorkflows[0]?.id || null
      const nextId = preferredId && nextWorkflows.some(workflow => workflow.id === preferredId)
        ? preferredId
        : fallbackId

      setWorkflows(nextWorkflows)
      setWorkflowTools(nextTools)
      setActiveWorkflowId(nextId)
      setActiveWorkflow(nextId ? await fetchWorkflow(backendApiUrl, nextId) : null)
      setWorkflowError('')
    } catch (error) {
      console.warn('Failed to load workflows', error)
      setWorkflowError(String(error?.message || error))
    }
  }

  useEffect(() => {
    if (!backendApiUrl) {
      setWorkflows([])
      setWorkflowTools([])
      setActiveWorkflowId(null)
      setActiveWorkflow(null)
      return
    }
    refreshWorkflows()
  }, [backendApiUrl])

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

    desktopApi.onWindowFocus(handleFocus);

    return () => {
      desktopApi.offWindowFocus(handleFocus);
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

  const fetchHistory = async (sessionId) => {
    if (!sessionId || !backendApiUrl) return null;
    try {
      const response = await fetch(`${backendApiUrl}/history?session_id=${encodeURIComponent(sessionId)}`)
      const data = await response.json()
      const historyMessages = (data.messages || []).map(message => ({
        ...message,
        id: message.message_id || message.id,
      }))
      setChatSessions(prevSessions =>
        prevSessions.map(session =>
          session.session_id === sessionId
            ? { ...session, messages: historyMessages }
            : session
        )
      );
      return historyMessages
    } catch {
      return null
    }
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

  async function handleSelectWorkflow(workflowId) {
    if (!backendApiUrl || !workflowId) return
    setActiveWorkflowId(workflowId)
    try {
      setActiveWorkflow(await fetchWorkflow(backendApiUrl, workflowId))
      setWorkflowError('')
    } catch (error) {
      console.warn('Failed to load workflow', error)
      setWorkflowError(String(error?.message || error))
    }
  }

  async function handleCreateWorkflow() {
    if (!backendApiUrl) return
    try {
      const created = await createWorkflow(backendApiUrl, { name: 'Untitled Workflow', graph: DEFAULT_WORKFLOW_GRAPH })
      await refreshWorkflows(created.id)
    } catch (error) {
      console.warn('Failed to create workflow', error)
      setWorkflowError(String(error?.message || error))
    }
  }

  async function handleDuplicateWorkflow(workflowId) {
    if (!backendApiUrl || !workflowId) return
    try {
      const created = await duplicateWorkflow(backendApiUrl, workflowId)
      await refreshWorkflows(created.id)
    } catch (error) {
      console.warn('Failed to duplicate workflow', error)
      setWorkflowError(String(error?.message || error))
    }
  }

  async function handleDeleteWorkflow(workflowId) {
    if (!backendApiUrl || !workflowId) return
    if (!window.confirm('Delete this workflow and its revisions?')) return
    try {
      await deleteWorkflow(backendApiUrl, workflowId)
      await refreshWorkflows(activeWorkflowId === workflowId ? null : activeWorkflowId)
    } catch (error) {
      console.warn('Failed to delete workflow', error)
      setWorkflowError(String(error?.message || error))
    }
  }

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

  function showKnowledgeSaveToast(message) {
    setKnowledgeSaveToast(message)
    window.setTimeout(() => setKnowledgeSaveToast(current => current === message ? '' : current), 4200)
  }

  function openKnowledgeSaveDialog(message, index) {
    if (!message?.message_id) {
      showKnowledgeSaveToast('This message is still being written to chat history. Try again in a moment.')
      return
    }
    let precedingUserMessage = null
    if (message.role === 'assistant') {
      for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
        if (messages[cursor]?.role === 'user') {
          precedingUserMessage = messages[cursor]
          break
        }
      }
    }
    setKnowledgeSaveTarget({ message, precedingUserMessage })
  }

  async function handleKnowledgeSaved(result) {
    setKnowledgeSaveTarget(null)
    await refreshLibraries()
    await refreshLibraryJobs()
    showKnowledgeSaveToast(result?.already_saved ? 'This message is already saved in that database.' : 'Message saved to knowledge.')
  }

  function openChatMessageSource(item) {
    const sessionId = String(item?.source_session_id || '')
    const messageId = String(item?.source_message_id || '')
    if (!sessionId || !chatSessions.some(session => session.session_id === sessionId)) {
      showKnowledgeSaveToast('The original chat is no longer available. The saved knowledge remains intact.')
      return
    }
    knowledgeJumpTargetRef.current = messageId
    setActiveSidebarMode('chats')
    selectChat(sessionId)
  }

  const chatModelPickerOptions = useMemo(() => {
    return buildModelPickerOptions(availableChatModels, model, 'saved model unavailable')
  }, [availableChatModels, model])

  const handleWorkflowRunEvent = React.useCallback((sessionId, event) => {
    if (
      event.type === 'tool_result'
      && String(event.payload?.tool || '').startsWith('heimgeist.save_')
      && event.payload?.result?.status === 'saved'
    ) {
      void refreshLibraries()
      void refreshLibraryJobs()
    }
    setWorkflowExecutions(previous => {
      const current = previous[sessionId] || {
        runId: event.run_id,
        workflowName: event.payload?.workflow?.name || 'Workflow',
        status: 'running',
        startedAt: Date.now(),
        events: [],
      }
      if (event.type === 'client_run_started' && current.runId !== event.run_id) {
        return {
          ...previous,
          [sessionId]: {
            runId: event.run_id,
            workflowName: event.payload?.workflow?.name || 'Workflow',
            status: 'running',
            startedAt: Date.now(),
            events: [event],
          },
        }
      }
      let status = current.status
      let finishedAt = current.finishedAt
      if (event.type === 'client_run_started') {
        status = 'running'
      } else if (event.type === 'run_completed') {
        status = 'completed'
        finishedAt = Date.now()
      } else if (event.type === 'run_failed') {
        status = 'failed'
        finishedAt = Date.now()
      } else if (event.type === 'run_cancelled') {
        status = 'cancelled'
        finishedAt = Date.now()
      } else if (event.type === 'confirmation_required') {
        status = 'waiting_confirmation'
        setPendingWorkflowConfirmation({ ...event, sessionId })
      }
      return {
        ...previous,
        [sessionId]: {
          ...current,
          runId: event.run_id || current.runId,
          workflowName: event.payload?.workflow?.name || current.workflowName,
          status,
          finishedAt,
          events: [...(current.events || []), event].slice(-120),
        },
      }
    })
  }, [backendApiUrl])

  const { regenerateFromIndex, sendMessage } = createChatGenerationHandlers({
    activeSessionId,
    activeSessionIdRef,
    backendApiUrl,
    attachWorkflowRunToRequest,
    beginCancelableRequest,
    canAttachImages,
    chatSessions,
    composerAttachments,
    createNewChat,
    finishCancelableRequest,
    getChatLibraryForSession,
    getChatWorkflowForSession,
    imageAttachmentUnavailableReason,
    input,
    isSending,
    model,
    onMessagesPersisted: fetchHistory,
    onWorkflowRunEvent: handleWorkflowRunEvent,
    restoredForRef,
    scrollMessageToTop,
    scrollToBottom,
    searxEngines,
    searxUrl,
    setChatSessions,
    setComposerAttachments,
    setInput,
    setIsAttachmentMenuOpen,
    setNewMsgTip,
    setPendingScrollToLastUser,
    setUnreadSessions,
    setUserScrolledUp,
    streamOutput,
    transcriptionModel,
    userScrolledUpRef,
    visionModel,
    webSearchEnabled,
    workflowRouterModel,
    workflowSelectionMode,
  })

  useEffect(() => {
    const messageId = knowledgeJumpTargetRef.current
    if (activeSidebarMode !== 'chats' || !messageId) return
    if (!messages.some(message => message.message_id === messageId)) return
    knowledgeJumpTargetRef.current = null
    requestAnimationFrame(() => scrollMessageToTop(messageId, 'smooth', activeSessionId))
  }, [activeSessionId, activeSidebarMode, messages, scrollMessageToTop])

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
            className={`sidebar-tab ${activeSidebarMode === 'workflows' ? 'active' : ''}`}
            onClick={() => handleSidebarClick('workflows')}
          >
            Flows
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
          {activeSidebarMode === 'workflows' && (
            <WorkflowList
              activeId={activeWorkflowId}
              onCreate={handleCreateWorkflow}
              onDelete={handleDeleteWorkflow}
              onDuplicate={handleDuplicateWorkflow}
              onSelect={handleSelectWorkflow}
              workflows={workflows}
            />
          )}
        </div>
        {(activeSidebarMode === 'chats' || activeSidebarMode === 'dbs') && (
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
                <span className="header-subtle">
                  {workflowSelectionMode !== 'manual'
                    ? 'Workflow: Auto'
                    : `Flow: ${selectedWorkflow?.name || 'Direct'}`}
                </span>
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
              <WorkflowExecutionPanel execution={workflowExecutions[activeSessionId]} />
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
                            <button className="icon-button knowledge-save-button" title="Save message to knowledge" aria-label="Save message to knowledge" onClick={() => openKnowledgeSaveDialog(m, i)}>
                              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M8.5 14.5A7 7 0 1 1 15.5 14.5C14.6 15.2 14 16.1 14 17h-4c0-.9-.6-1.8-1.5-2.5Z"/></svg>
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
                            <button className="icon-button knowledge-save-button" title="Save message to knowledge" aria-label="Save message to knowledge" onClick={() => openKnowledgeSaveDialog(m, i)}>
                              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M8.5 14.5A7 7 0 1 1 15.5 14.5C14.6 15.2 14 16.1 14 17h-4c0-.9-.6-1.8-1.5-2.5Z"/></svg>
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
                {workflowSelectionMode === 'manual' && (
                  <WorkflowSelector
                    disabled={isSending}
                    selection={chatWorkflowSelection}
                    workflows={workflows}
                    onChange={(nextSelection) => setChatWorkflowForSession(activeSessionId, nextSelection)}
                  />
                )}
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
              onOpenChatMessage={openChatMessageSource}
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
            onWorkflowRouterModelChange={setWorkflowRouterModel}
            onWorkflowSelectionModeChange={setWorkflowSelectionMode}
            searxEngines={searxEngines}
            searxUrl={searxUrl}
            setSearxEngines={setSearxEngines}
            setSearxUrl={setSearxUrl}
            streamOutput={streamOutput}
          />
        )}
        {activeSidebarMode === 'workflows' && (
          <WorkflowsArea
            apiBase={backendApiUrl}
            error={workflowError}
            model={model}
            onChanged={refreshWorkflows}
            tools={workflowTools}
            workflow={activeWorkflow}
          />
        )}
        {knowledgeSaveTarget && (
          <SaveMessageToKnowledgeDialog
            apiBase={backendApiUrl}
            defaultLibrarySlug={chatLibrarySlug || activeLibrarySlug}
            libraries={libraries}
            message={knowledgeSaveTarget.message}
            onClose={() => setKnowledgeSaveTarget(null)}
            onSaved={handleKnowledgeSaved}
            precedingUserMessage={knowledgeSaveTarget.precedingUserMessage}
          />
        )}
        {knowledgeSaveToast && <div className="knowledge-save-toast" role="status">{knowledgeSaveToast}</div>}
        <WorkflowConfirmationDialog
          confirmation={pendingWorkflowConfirmation}
          onRespond={async (approved) => {
            const confirmation = pendingWorkflowConfirmation
            if (!confirmation) return
            try {
              await respondToConfirmation(
                backendApiUrl,
                confirmation.run_id,
                confirmation.payload?.confirmation_id,
                approved,
              )
              setPendingWorkflowConfirmation(null)
              setWorkflowExecutions(previous => ({
                ...previous,
                [confirmation.sessionId]: {
                  ...(previous[confirmation.sessionId] || {}),
                  status: 'running',
                },
              }))
            } catch (error) {
              window.alert(error.message)
            }
          }}
        />
      </div>
    </div>
  )
}
