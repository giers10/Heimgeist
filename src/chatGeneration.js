import { flushSync } from 'react-dom'
import { attachmentIsImage, getAttachmentDisplayName } from './attachments'
import { getErrorText, isAbortError, readBackendErrorText } from './backendApi'
import {
  fetchLocalLibraryContext,
  fetchWebSearchContext,
  postChatMessage,
  postRegenerateMessage,
  requestGeneratedTitle,
} from './chatApi'
import { sanitizeChatTitle } from './chatText'
import { createWorkflowRun, streamWorkflowEvents } from './workflows/workflowApi'

function messageHasImageAttachments(message) {
  return Array.isArray(message?.attachments) && message.attachments.some(attachmentIsImage)
}

function buildAttachmentTitleSeed(text, attachments = []) {
  const trimmed = String(text || '').trim()
  if (trimmed) {
    return trimmed
  }
  const firstAttachment = Array.isArray(attachments) ? attachments[0] : null
  const firstName = getAttachmentDisplayName(firstAttachment, '')
  return firstName || 'Attachment'
}

function setAssistantMessageContent(setChatSessions, sessionId, messageId, content, options = {}) {
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

export function createChatGenerationHandlers({
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
  onMessagesPersisted,
  onWorkflowRunEvent,
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
}) {
  function getEffectiveWorkflowSelection(sessionId) {
    if (workflowSelectionMode !== 'manual') {
      return { mode: 'automatic', workflowId: null }
    }
    const selection = getChatWorkflowForSession?.(sessionId)
    if (selection?.mode === 'explicit') {
      return selection
    }
    return { mode: 'direct', workflowId: null }
  }

  async function executeWorkflowRun({
    sessionId,
    userMessage,
    attachments,
    requestController,
    selection,
    workflowRevisionId = null,
    regenerateIndex = null,
    isNewChat = false,
  }) {
    const selectedLibrary = getChatLibraryForSession(sessionId)
    const created = await createWorkflowRun(backendApiUrl, {
      session_id: sessionId,
      message: userMessage.content,
      model,
      selection_mode: selection.mode,
      workflow_id: selection.workflowId || null,
      workflow_revision_id: workflowRevisionId,
      library_slug: selectedLibrary?.slug || null,
      web_search_enabled: Boolean(webSearchEnabled),
      searx_url: searxUrl || null,
      searx_engines: searxEngines || [],
      attachments,
      vision_model: visionModel || null,
      transcription_model: transcriptionModel || null,
      router_model: workflowRouterModel || null,
      regenerate_index: regenerateIndex,
      stream: true,
    }, requestController.signal)
    attachWorkflowRunToRequest?.(requestController, created.run_id)
    const assistantMsgId = `workflow-${created.run_id}`
    let fullReply = ''
    setChatSessions(previous => previous.map(session => session.session_id === sessionId
      ? { ...session, messages: [...(session.messages || []), {
          role: 'assistant', content: '', id: assistantMsgId, sources: [],
          workflow_id: created.workflow?.id,
          workflow_revision_id: created.workflow?.revision_id,
          workflow_run_id: created.run_id,
        }] }
      : session))
    onWorkflowRunEvent?.(sessionId, {
      type: 'client_run_started',
      run_id: created.run_id,
      payload: { workflow: created.workflow },
      timestamp: new Date().toISOString(),
    })
    await streamWorkflowEvents(backendApiUrl, created.run_id, {
      signal: requestController.signal,
      onEvent: event => {
        onWorkflowRunEvent?.(sessionId, event)
        if (event.type === 'model_token') {
          fullReply += event.payload?.text || ''
          setAssistantMessageContent(setChatSessions, sessionId, assistantMsgId, fullReply)
        } else if (event.type === 'run_completed') {
          const finalOutput = event.payload?.output || null
          const content = finalOutput?.content || fullReply
          setChatSessions(previous => previous.map(session => {
            if (session.session_id !== sessionId) return session
            return {
              ...session,
              messages: (session.messages || []).map(message => message.id === assistantMsgId
                ? { ...message, content, sources: finalOutput?.sources || message.sources || [] }
                : message),
            }
          }))
        } else if (event.type === 'run_failed') {
          const message = event.payload?.message || 'Workflow execution failed.'
          setAssistantMessageContent(setChatSessions, sessionId, assistantMsgId, `Error: ${message}`)
        } else if (event.type === 'run_cancelled') {
          setAssistantMessageContent(setChatSessions, sessionId, assistantMsgId, fullReply, { removeIfEmpty: true })
        }
      },
    })
    if (onMessagesPersisted) await onMessagesPersisted(sessionId)
    if (activeSessionIdRef.current !== sessionId) {
      setUnreadSessions(previous => [...new Set([...previous, sessionId])])
    } else if (!userScrolledUpRef.current[sessionId]) {
      requestAnimationFrame(() => scrollMessageToTop(assistantMsgId, 'smooth', sessionId))
    }
    if (isNewChat) {
      requestGeneratedTitle({
        apiBase: backendApiUrl,
        message: buildAttachmentTitleSeed(userMessage.content, attachments),
        model,
        sessionId,
      })
        .then(response => response.json())
        .then(data => {
          const sanitizedTitle = sanitizeChatTitle(data.title)
          setChatSessions(previous => previous.map(session =>
            session.session_id === sessionId ? { ...session, name: sanitizedTitle } : session
          ))
        })
    }
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

    if (getChatWorkflowForSession) {
      try {
        const currentSelection = getEffectiveWorkflowSelection(sessionId)
        const originalAssistant = msgs.slice(lastUserIdx + 1).find(message => message?.role === 'assistant')
        const originalWorkflowId = originalAssistant?.workflow_id || null
        const originalSelectionMode = originalAssistant?.agent_summary?.selection_mode || null
        let selection = currentSelection
        let workflowRevisionId = null
        if (currentSelection.mode === 'automatic' && originalSelectionMode === 'automatic' && originalWorkflowId) {
          selection = { mode: 'explicit', workflowId: originalWorkflowId }
          workflowRevisionId = originalAssistant?.workflow_revision_id || null
        } else if (
          originalWorkflowId
          && (
            (currentSelection.mode === 'explicit' && originalSelectionMode === 'explicit' && currentSelection.workflowId === originalWorkflowId)
            || (currentSelection.mode === 'direct' && originalSelectionMode === 'direct')
          )
        ) {
          workflowRevisionId = originalAssistant?.workflow_revision_id || null
        }
        await executeWorkflowRun({
          sessionId,
          userMessage: { ...msgs[lastUserIdx], content: overrideUserText ?? msgs[lastUserIdx]?.content ?? '' },
          attachments: msgs[lastUserIdx]?.attachments || [],
          requestController,
          selection,
          workflowRevisionId,
          regenerateIndex: lastUserIdx,
        })
      } catch (error) {
        if (!isAbortError(error)) console.error('Failed to regenerate workflow response:', error)
      } finally {
        finishCancelableRequest(requestController)
      }
      return
    }

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
            setAssistantMessageContent(setChatSessions, sessionId, assistantMsgId, full)

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
            setAssistantMessageContent(setChatSessions, sessionId, assistantMsgId, full, { removeIfEmpty: true })
            return
          }

          console.error(error)
          setAssistantMessageContent(setChatSessions, sessionId, assistantMsgId, `Error: ${getErrorText(error)}`, { removeIfEmpty: true })
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
      if (onMessagesPersisted) void onMessagesPersisted(sessionId)
    } catch (error) {
      if (!isAbortError(error)) {
        console.error(error)
      }
    } finally {
      finishCancelableRequest(requestController)
    }
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
      id: `msg-${Date.now()}-${Math.random()}`,
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
      if (getChatWorkflowForSession) {
        const selection = getEffectiveWorkflowSelection(targetSessionId)
        await executeWorkflowRun({
          sessionId: targetSessionId,
          userMessage: userMsg,
          attachments: outgoingAttachments,
          requestController,
          selection,
          isNewChat,
        })
        return
      }

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
              setAssistantMessageContent(setChatSessions, targetSessionId, assistantMsgId, fullReply)

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
            setAssistantMessageContent(setChatSessions, targetSessionId, assistantMsgId, fullReply)

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
            setAssistantMessageContent(setChatSessions, targetSessionId, assistantMsgId, fullReply, { removeIfEmpty: true })
            return
          }

          console.error('Failed to send message:', error)
          if (Number(error?.status) >= 400 && Number(error?.status) < 500) {
            setAssistantMessageContent(setChatSessions, targetSessionId, assistantMsgId, fullReply, { removeIfEmpty: true })
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
          setAssistantMessageContent(setChatSessions, targetSessionId, assistantMsgId, 'Error: ' + getErrorText(error), { removeIfEmpty: true })
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
          sources: citationSources,
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
      if (onMessagesPersisted) void onMessagesPersisted(targetSessionId)

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

  return {
    regenerateFromIndex,
    sendMessage,
  }
}
