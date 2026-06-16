import React, { useEffect, useState } from 'react'
import { splitThinkBlocks } from './chatText'

function cleanAssistantContent(content) {
  const raw = String(content || '')
  try {
    const { answer } = splitThinkBlocks(raw)
    return String(answer || raw).trim()
  } catch {
    return raw.trim()
  }
}

function defaultTitle(message) {
  const prefix = message?.role === 'user' ? 'User note' : 'Saved answer'
  const content = message?.role === 'assistant'
    ? cleanAssistantContent(message?.content)
    : String(message?.content || '').trim()
  const compact = content.replace(/\s+/g, ' ')
  if (!compact) return prefix
  const excerpt = compact.length > 90 ? `${compact.slice(0, 87).trim()}...` : compact
  return `${prefix}: ${excerpt}`
}

function defaultKnowledgeContent(message, precedingUserMessage) {
  const content = message?.role === 'assistant'
    ? cleanAssistantContent(message?.content)
    : String(message?.content || '').trim()
  if (message?.role !== 'assistant' || !precedingUserMessage?.content?.trim()) {
    return content
  }
  return `User question:\n${precedingUserMessage.content.trim()}\n\nAssistant response:\n${content}`
}

async function expectJson(response) {
  const raw = await response.text()
  let data = null
  try {
    data = raw ? JSON.parse(raw) : null
  } catch {}
  if (!response.ok) {
    throw new Error(data?.detail || raw || `HTTP ${response.status}`)
  }
  return data
}

export default function SaveMessageToKnowledgeDialog({
  apiBase,
  message,
  onClose,
  onSaved,
  precedingUserMessage,
}) {
  const [title, setTitle] = useState(() => defaultTitle(message))
  const [content, setContent] = useState(() => defaultKnowledgeContent(message, precedingUserMessage))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const onKeyDown = event => {
      if (event.key === 'Escape' && !busy) onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [busy, onClose])

  async function submit(event) {
    event.preventDefault()
    if (!message?.message_id) return
    setBusy(true)
    setError('')
    try {
      const response = await fetch(`${apiBase}/knowledge/chat-messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message_id: message.message_id,
          title,
          content,
        }),
      })
      const data = await expectJson(response)
      await onSaved(data)
    } catch (submitError) {
      setError(String(submitError?.message || submitError))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="knowledge-save-overlay" role="presentation" onMouseDown={event => {
      if (event.target === event.currentTarget && !busy) onClose()
    }}>
      <form className="knowledge-save-dialog" role="dialog" aria-modal="true" aria-labelledby="knowledge-save-title" onSubmit={submit}>
        <div className="knowledge-save-header">
          <div>
            <strong id="knowledge-save-title">Save message to knowledge</strong>
            <p>The saved snapshot becomes a normal item in Knowledge.</p>
          </div>
          <button type="button" className="icon-button" title="Close" aria-label="Close" onClick={onClose} disabled={busy}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>

        <label>
          Title
          <input value={title} onChange={event => setTitle(event.target.value)} required autoFocus disabled={busy} />
        </label>
        <label>
          Knowledge content
          <textarea value={content} onChange={event => setContent(event.target.value)} rows={12} required disabled={busy} />
        </label>

        {error && <div className="form-error">{error}</div>}
        <div className="knowledge-save-actions">
          <button type="button" className="button ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="button" disabled={busy || !title.trim() || !content.trim()}>
            {busy ? 'Saving...' : 'Save to Knowledge'}
          </button>
        </div>
      </form>
    </div>
  )
}
