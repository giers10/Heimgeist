import { useEffect, useMemo, useState } from 'react'
import { CHAT_WORKFLOW_MAP_KEY } from './appConfig'

const DIRECT_SELECTION = Object.freeze({ mode: 'direct', workflowId: null })

function loadMap() {
  try {
    return JSON.parse(localStorage.getItem(CHAT_WORKFLOW_MAP_KEY) || '{}')
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
        if (selection?.mode === 'explicit' && !validIds.has(selection.workflowId)) {
          next[sessionId] = DIRECT_SELECTION
          changed = true
        }
      })
      return changed ? next : previous
    })
  }, [workflows])

  const selection = activeSessionId
    ? (selectionBySession[activeSessionId] || DIRECT_SELECTION)
    : DIRECT_SELECTION

  const selectedWorkflow = useMemo(() => {
    if (selection.mode === 'direct') return workflows.find(item => item.slug === 'input-output') || null
    if (selection.mode === 'automatic') return null
    return workflows.find(item => item.id === selection.workflowId) || null
  }, [selection, workflows])

  function getSelectionForSession(sessionId) {
    return selectionBySession[sessionId] || DIRECT_SELECTION
  }

  function setSelectionForSession(sessionId, nextSelection) {
    if (!sessionId) return
    setSelectionBySession(previous => ({
      ...previous,
      [sessionId]: nextSelection?.mode ? nextSelection : DIRECT_SELECTION,
    }))
  }

  return {
    getSelectionForSession,
    selectedWorkflow,
    selection,
    setSelectionForSession,
  }
}
