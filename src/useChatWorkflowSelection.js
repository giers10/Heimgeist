import { useEffect, useMemo, useState } from 'react'
import { CHAT_WORKFLOW_MAP_KEY } from './appConfig'

const DIRECT_SELECTION = Object.freeze({ mode: 'direct', workflowId: null })

function normalizeSelection(selection, validIds = null) {
  if (selection?.mode === 'explicit' && (!validIds || validIds.has(selection.workflowId))) {
    return selection
  }
  return DIRECT_SELECTION
}

function loadMap() {
  try {
    const raw = JSON.parse(localStorage.getItem(CHAT_WORKFLOW_MAP_KEY) || '{}')
    return Object.fromEntries(Object.entries(raw).map(([sessionId, selection]) => [sessionId, normalizeSelection(selection)]))
  } catch {
    return {}
  }
}

export function useChatWorkflowSelection({ activeSessionId, workflows }) {
  const [selectionBySession, setSelectionBySession] = useState(loadMap)

  useEffect(() => {
    try {
      localStorage.setItem(CHAT_WORKFLOW_MAP_KEY, JSON.stringify(selectionBySession))
    } catch {}
  }, [selectionBySession])

  useEffect(() => {
    const validIds = new Set(workflows.filter(item => item.enabled).map(item => item.id))
    setSelectionBySession(previous => {
      let changed = false
      const next = { ...previous }
      Object.entries(next).forEach(([sessionId, selection]) => {
        const normalized = normalizeSelection(selection, validIds)
        if (normalized !== selection) {
          next[sessionId] = DIRECT_SELECTION
          changed = true
        }
      })
      return changed ? next : previous
    })
  }, [workflows])

  const selection = activeSessionId
    ? normalizeSelection(selectionBySession[activeSessionId])
    : DIRECT_SELECTION

  const selectedWorkflow = useMemo(() => {
    if (selection.mode === 'direct') return workflows.find(item => item.slug === 'input-output') || null
    if (selection.mode === 'automatic') return null
    return workflows.find(item => item.id === selection.workflowId) || null
  }, [selection, workflows])

  function getSelectionForSession(sessionId) {
    return normalizeSelection(selectionBySession[sessionId])
  }

  function setSelectionForSession(sessionId, nextSelection) {
    if (!sessionId) return
    setSelectionBySession(previous => ({
      ...previous,
      [sessionId]: normalizeSelection(nextSelection),
    }))
  }

  return {
    getSelectionForSession,
    selectedWorkflow,
    selection,
    setSelectionForSession,
  }
}
