import React, { useEffect, useMemo, useRef, useState } from 'react'
import desktopApi from './desktop/desktopApi'

const TOAST_DURATION_MS = {
  info: 3600,
  success: 4800,
  warning: 5600
}

function itemSyncMeta(item) {
  const sync = item?.sync || {}
  const status = String(sync.status || 'pending')
  const progress = Math.max(0, Math.min(100, Number(sync.progress) || 0))
  const detail = String(sync.detail || '').trim()
  const enrichEnabled = !!item?.enrich_enabled
  const metadataReady = ['ready', 'fallback'].includes(item?.metadata?.status)

  if (status === 'ready') {
    return {
      status,
      progress: 100,
      label: 'Available',
      detail: detail || (
        metadataReady
          ? (enrichEnabled ? 'Ready with standard metadata and deep enrichment.' : 'Ready with standard metadata.')
          : 'Ready for chat. Automatic metadata has not been generated yet.'
      )
    }
  }
  if (status === 'failed') {
    return {
      status,
      progress: 100,
      label: 'Sync failed',
      detail: String(sync.error || '').trim() || detail || 'Heimgeist could not finish syncing this content.'
    }
  }
  if (status === 'syncing') {
    return {
      status,
      progress,
      label: progress > 0 ? `Syncing ${Math.round(progress)}%` : 'Syncing',
      detail: detail || 'Rebuilding the database search indexes.'
    }
  }
  return {
    status: 'pending',
    progress: 6,
    label: 'Queued',
    detail: 'Waiting to rebuild the retrieval pipeline.'
  }
}

function kindLabel(item) {
  if (item?.kind === 'text') return 'Text'
  if (item?.kind === 'website') return 'Website'
  if (item?.kind === 'video') return 'Video'
  if (item?.kind === 'chat_message') return 'Chat Message'
  return 'File'
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

export default function LibraryManager({ apiBase, library, jobs, onOpenChatMessage, onRefresh }) {
  const [busy, setBusy] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')
  const [toasts, setToasts] = useState([])
  const [search, setSearch] = useState('')
  const [formMode, setFormMode] = useState(null)
  const [editingItemId, setEditingItemId] = useState(null)
  const [textTitle, setTextTitle] = useState('')
  const [textContent, setTextContent] = useState('')
  const [websiteTitle, setWebsiteTitle] = useState('')
  const [websiteUrl, setWebsiteUrl] = useState('')
  const [websiteProcessing, setWebsiteProcessing] = useState(false)
  const [expandedItemId, setExpandedItemId] = useState(null)
  const [contentPreviews, setContentPreviews] = useState({})
  const toastTimeoutsRef = useRef(new Map())
  const toastIdRef = useRef(0)
  const previousLibraryStateRef = useRef(null)

  useEffect(() => {
    setErrorMessage('')
    setSearch('')
    setExpandedItemId(null)
    setContentPreviews({})
    closeForm()
  }, [library?.slug, library?.name])

  function dismissToast(id) {
    const timeoutId = toastTimeoutsRef.current.get(id)
    if (timeoutId) clearTimeout(timeoutId)
    toastTimeoutsRef.current.delete(id)
    setToasts(current => current.filter(toast => toast.id !== id))
  }

  function clearToasts() {
    toastTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId))
    toastTimeoutsRef.current.clear()
    setToasts([])
  }

  function queueToast(message, tone = 'info') {
    setToasts(current => {
      if (current.some(toast => toast.message === message && toast.tone === tone)) return current
      const id = `library-toast-${toastIdRef.current++}`
      const timeoutId = window.setTimeout(() => dismissToast(id), TOAST_DURATION_MS[tone] || TOAST_DURATION_MS.info)
      toastTimeoutsRef.current.set(id, timeoutId)
      return [...current, { id, message, tone }].slice(-3)
    })
  }

  useEffect(() => () => {
    toastTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId))
    toastTimeoutsRef.current.clear()
  }, [])

  function closeForm() {
    setFormMode(null)
    setEditingItemId(null)
    setTextTitle('')
    setTextContent('')
    setWebsiteTitle('')
    setWebsiteUrl('')
  }

  async function toggleContentPreview(item) {
    const itemId = item?.item_id
    if (!library || !itemId) return
    if (expandedItemId === itemId) {
      setExpandedItemId(null)
      return
    }
    setExpandedItemId(itemId)
    if (contentPreviews[itemId]) return
    setContentPreviews(current => ({ ...current, [itemId]: { loading: true } }))
    try {
      const response = await fetch(`${apiBase}/libraries/${library.slug}/items/${itemId}`)
      const data = await expectJson(response)
      setContentPreviews(current => ({ ...current, [itemId]: { data } }))
    } catch (error) {
      setContentPreviews(current => ({
        ...current,
        [itemId]: { error: String(error?.message || error) }
      }))
    }
  }

  async function runAction(fn, successMessage) {
    setBusy(true)
    setErrorMessage('')
    try {
      const result = await fn()
      if (successMessage) queueToast(successMessage, 'success')
      return result
    } catch (error) {
      setErrorMessage(String(error?.message || error))
      throw error
    } finally {
      setBusy(false)
      await onRefresh()
    }
  }

  async function addPaths() {
    if (!library) return
    const paths = await desktopApi.pickPaths()
    if (!Array.isArray(paths) || paths.length === 0) return
    try {
      await runAction(async () => {
        const response = await fetch(`${apiBase}/libraries/${library.slug}/files/register`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ paths })
        })
        return expectJson(response)
      }, 'Files added. Heimgeist is updating the database.')
    } catch {}
  }

  async function submitText(event) {
    event.preventDefault()
    if (!library) return
    try {
      await runAction(async () => {
        const editing = Boolean(editingItemId)
        const response = await fetch(
          editing
            ? `${apiBase}/libraries/${library.slug}/texts/${editingItemId}`
            : `${apiBase}/libraries/${library.slug}/texts`,
          {
            method: editing ? 'PATCH' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: textTitle, content: textContent })
          }
        )
        return expectJson(response)
      }, editingItemId ? 'Text updated.' : 'Text added to the database.')
      if (editingItemId) {
        setContentPreviews(current => {
          const next = { ...current }
          delete next[editingItemId]
          return next
        })
        setExpandedItemId(current => current === editingItemId ? null : current)
      }
      closeForm()
    } catch {}
  }

  async function editText(item) {
    if (!library || !item?.item_id) return
    setBusy(true)
    setErrorMessage('')
    try {
      const response = await fetch(`${apiBase}/libraries/${library.slug}/items/${item.item_id}`)
      const data = await expectJson(response)
      setEditingItemId(item.item_id)
      setTextTitle(data.title || item.title || item.name || '')
      setTextContent(data.content || '')
      setFormMode('text')
    } catch (error) {
      setErrorMessage(String(error?.message || error))
    } finally {
      setBusy(false)
    }
  }

  async function submitWebsite(event) {
    event.preventDefault()
    if (!library) return
    setWebsiteProcessing(true)
    try {
      const result = await runAction(async () => {
        const response = await fetch(`${apiBase}/libraries/${library.slug}/websites`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: websiteUrl, title: websiteTitle || null })
        })
        return expectJson(response)
      })
      queueToast(
        result?.content_kind === 'video'
          ? 'Video transcribed and summarized. Heimgeist is indexing it.'
          : 'Website saved. Heimgeist is indexing its text.',
        'success'
      )
      closeForm()
    } catch {
    } finally {
      setWebsiteProcessing(false)
    }
  }

  async function refreshWebsite(item) {
    if (!library || !item?.item_id) return
    try {
      const result = await runAction(async () => {
        const response = await fetch(`${apiBase}/libraries/${library.slug}/websites/${item.item_id}/refresh`, {
          method: 'POST'
        })
        return expectJson(response)
      })
      setContentPreviews(current => {
        const next = { ...current }
        delete next[item.item_id]
        return next
      })
      setExpandedItemId(current => current === item.item_id ? null : current)
      queueToast(result?.changed ? 'Website changed and is being reindexed.' : 'Website checked. No text changes found.', 'success')
    } catch {}
  }

  async function refreshVideo(item) {
    if (!library || !item?.item_id) return
    setWebsiteProcessing(true)
    try {
      const result = await runAction(async () => {
        const response = await fetch(`${apiBase}/libraries/${library.slug}/videos/${item.item_id}/refresh`, {
          method: 'POST'
        })
        return expectJson(response)
      })
      setContentPreviews(current => {
        const next = { ...current }
        delete next[item.item_id]
        return next
      })
      setExpandedItemId(current => current === item.item_id ? null : current)
      queueToast(result?.changed ? 'Video reprocessed and changed.' : 'Video reprocessed. Transcript content is unchanged.', 'success')
    } catch {
    } finally {
      setWebsiteProcessing(false)
    }
  }

  async function removeItem(item) {
    if (!library) return
    try {
      await runAction(async () => {
        const response = await fetch(`${apiBase}/libraries/${library.slug}/files`, {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rel: item.rel })
        })
        return expectJson(response)
      }, `${kindLabel(item)} removed.`)
    } catch {}
  }

  async function updateEnrichment(item, enabled) {
    if (!library) return
    try {
      await runAction(async () => {
        const response = await fetch(`${apiBase}/libraries/${library.slug}/files/enrichment`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rel: item.rel, enabled })
        })
        return expectJson(response)
      }, enabled ? 'Deep enrichment enabled. Metadata is being updated.' : 'Using standard metadata only.')
    } catch {}
  }

  async function generateMetadata() {
    if (!library) return
    try {
      await runAction(async () => {
        const response = await fetch(`${apiBase}/libraries/${library.slug}/jobs/prepare`, { method: 'POST' })
        return expectJson(response)
      }, 'Generating metadata for this database.')
    } catch {}
  }

  async function retrySync() {
    if (!library) return
    try {
      await runAction(async () => {
        const response = await fetch(`${apiBase}/libraries/${library.slug}/jobs/prepare`, { method: 'POST' })
        return expectJson(response)
      })
    } catch {}
  }

  const librarySlug = library?.slug || null
  const isSyncing = !!librarySlug && (jobs || []).some(
    job => job.slug === librarySlug && (job.status === 'queued' || job.status === 'running')
  )
  const isReadyForChat = !!library?.states?.is_indexed
  const items = library?.files || []
  const hasFailedItems = items.some(item => item?.sync?.status === 'failed')
  const filteredItems = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (!term) return items
    return items.filter(item => [
      item.title,
      item.name,
      item.path,
      item.url,
      item.kind,
      item.source_session_title,
      item.source_role,
      item.channel,
      item.video_summary,
      item.metadata?.headline,
      item.metadata?.summary,
      ...(item.metadata?.keywords || []),
      ...(item.metadata?.entities || []).map(entity => entity?.name)
    ]
      .some(value => String(value || '').toLowerCase().includes(term)))
  }, [items, search])
  const hasMissingMetadata = items.some(item => !['ready', 'fallback'].includes(item?.metadata?.status))

  useEffect(() => {
    if (!library?.slug) {
      previousLibraryStateRef.current = null
      clearToasts()
      return
    }
    const nextState = {
      slug: library.slug,
      hasItems: items.length > 0,
      isSyncing,
      isReadyForChat,
      hasFailedItems
    }
    const previousState = previousLibraryStateRef.current
    if (!previousState || previousState.slug !== nextState.slug) {
      previousLibraryStateRef.current = nextState
      return
    }
    if (!previousState.isSyncing && nextState.isSyncing) {
      queueToast('Syncing this database. Heimgeist is rebuilding its search indexes.')
    } else if (previousState.isSyncing && !nextState.isSyncing) {
      if (nextState.hasFailedItems) queueToast('Some content did not finish syncing.', 'warning')
      else if (nextState.isReadyForChat) queueToast('Sync complete. This database is ready in chat.', 'success')
    }
    previousLibraryStateRef.current = nextState
  }, [library?.slug, items.length, hasFailedItems, isReadyForChat, isSyncing])

  if (!library) {
    return <div className="placeholder-view"><p>Create a database, then add files, your own texts, or websites.</p></div>
  }

  return (
    <div className="library-panel">
      <div className="library-panel-scroll">
        {errorMessage && <div className="form-error">{errorMessage}</div>}

        {formMode === 'text' && (
          <form className="library-inline-form library-content-form" onSubmit={submitText}>
            <div className="library-form-header">
              <strong>{editingItemId ? 'Edit text' : 'Add text'}</strong>
              <button type="button" className="button ghost" onClick={closeForm}>Cancel</button>
            </div>
            <label>
              Title
              <input value={textTitle} onChange={event => setTextTitle(event.target.value)} autoFocus required />
            </label>
            <label>
              Text
              <textarea value={textContent} onChange={event => setTextContent(event.target.value)} rows={12} required />
            </label>
            <div className="library-form-actions">
              <button className="button" type="submit" disabled={busy}>{editingItemId ? 'Save Changes' : 'Add Text'}</button>
            </div>
          </form>
        )}

        {formMode === 'website' && (
          <form className="library-inline-form library-content-form" onSubmit={submitWebsite}>
            <div className="library-form-header">
              <strong>Add website</strong>
              <button type="button" className="button ghost" onClick={closeForm}>Cancel</button>
            </div>
            <label>
              Website URL
              <input type="url" value={websiteUrl} onChange={event => setWebsiteUrl(event.target.value)} placeholder="https://example.com/article" autoFocus required />
            </label>
            <label>
              Custom title <span className="muted-copy">(optional)</span>
              <input value={websiteTitle} onChange={event => setWebsiteTitle(event.target.value)} />
            </label>
            <p className="muted-copy">Heimgeist saves a local text snapshot of this page. It will not crawl linked pages.</p>
            <div className="library-form-actions">
              <button className="button" type="submit" disabled={busy}>{busy ? 'Fetching…' : 'Save Website'}</button>
            </div>
          </form>
        )}

        <div className="library-content-heading">
          <div>
            <h2>Contents</h2>
            <p className="muted-copy">Files, texts, and website snapshots are searched together when this database is selected.</p>
          </div>
          <input
            className="library-search"
            value={search}
            onChange={event => setSearch(event.target.value)}
            placeholder="Search contents"
            aria-label="Search database contents"
          />
        </div>

        {filteredItems.length ? (
          <div className="library-content-grid">
            {filteredItems.map(item => {
              const sync = itemSyncMeta(item)
              const label = kindLabel(item)
              const source = item.kind === 'website'
                ? item.url
                : item.kind === 'text'
                  ? 'Written in Heimgeist'
                  : item.kind === 'chat_message'
                    ? `${item.source_role === 'user' ? 'User message' : 'Assistant message'} · ${item.source_session_title || 'Chat'}`
                    : item.path
              const itemId = item.item_id || item.sha256 || item.rel
              const isExpanded = expandedItemId === itemId
              const preview = contentPreviews[itemId]
              const metadata = item.metadata || {}
              const keywords = Array.isArray(metadata.keywords) ? metadata.keywords : []
              const entities = Array.isArray(metadata.entities) ? metadata.entities : []
              const qaPairs = Array.isArray(metadata.qa) ? metadata.qa : []
              const qualityFlags = Array.isArray(metadata.quality_flags) ? metadata.quality_flags : []
              return (
                <article key={itemId} className={`library-content-card kind-${item.kind || 'file'} ${isExpanded ? 'expanded' : ''}`}>
                  <div className="library-content-card-header">
                    <span className="library-kind-badge">{label}</span>
                    <span className={`library-file-sync-label ${sync.status}`}>{sync.label}</span>
                  </div>
                  <div className="library-file-name">{item.title || item.name || item.path}</div>
                  <div className="library-file-path">{source}</div>
                  <div className={`library-file-mode ${item.enrich_enabled ? 'enabled' : ''}`}>
                    {item.enrich_enabled ? 'Deep enrichment' : 'Standard'}
                  </div>
                  <div className={`library-item-metadata status-${metadata.status || 'missing'}`}>
                    {metadata.headline && <h3>{metadata.headline}</h3>}
                    {metadata.summary ? (
                      <p>{metadata.summary}</p>
                    ) : (
                      <p className="muted-copy">
                        {metadata.status === 'pending' ? 'Metadata is being generated.' : 'No automatic metadata has been generated yet.'}
                      </p>
                    )}
                    {keywords.length > 0 && (
                      <div className="library-metadata-chips" aria-label="Keywords">
                        {keywords.map(keyword => <span key={keyword}>{keyword}</span>)}
                      </div>
                    )}
                    {entities.length > 0 && (
                      <div className="library-metadata-entities">
                        {entities.map(entity => (
                          <span key={`${entity.name}-${entity.type}`}>{entity.name}{entity.type ? ` · ${entity.type}` : ''}</span>
                        ))}
                      </div>
                    )}
                    {qaPairs.length > 0 && (
                      <div className="library-metadata-qa">
                        <div className="library-metadata-label">Generated Q&amp;A</div>
                        {qaPairs.map((qa, index) => (
                          <div className="library-metadata-qa-pair" key={`${qa.q}-${index}`}>
                            <strong>{qa.q}</strong>
                            <span>{qa.a}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {(metadata.model || metadata.language || metadata.strategy || qualityFlags.length > 0) && (
                      <div className="library-metadata-details">
                        {metadata.model && <span>Model: {metadata.model}</span>}
                        {metadata.language && <span>Language: {metadata.language}</span>}
                        {metadata.strategy && <span>Strategy: {metadata.strategy}</span>}
                        {qualityFlags.length > 0 && <span>Flags: {qualityFlags.join(', ')}</span>}
                      </div>
                    )}
                    {metadata.status === 'fallback' && <div className="library-metadata-warning">Local model metadata was unavailable; Heimgeist used a text fallback.</div>}
                    {metadata.status === 'failed' && <div className="library-metadata-warning">{metadata.error || 'Metadata generation failed.'}</div>}
                  </div>
                  <div className="library-file-sync">
                    <div className="library-file-sync-detail">{sync.detail}</div>
                    <div className={`library-file-progress ${sync.status}`} role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(sync.progress)}>
                      <div className="library-file-progress-bar" style={{ width: `${sync.progress}%` }} />
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="library-content-preview">
                      <div className="library-content-preview-label">
                        {item.kind === 'website' ? 'Saved website text' : item.kind === 'chat_message' ? 'Stored chat knowledge' : item.kind === 'text' ? 'Stored text' : 'Extracted text used by RAG'}
                      </div>
                      {preview?.loading && <div className="muted-copy">Loading content…</div>}
                      {preview?.error && <div className="form-error">{preview.error}</div>}
                      {preview?.data && (
                        <>
                          <pre>{preview.data.content || 'No readable text was extracted from this item.'}</pre>
                          {preview.data.content_truncated && <div className="muted-copy">Preview shortened. The indexed source contains more text.</div>}
                        </>
                      )}
                    </div>
                  )}
                  <div className="library-file-actions">
                    <button className="button ghost" disabled={busy} onClick={() => toggleContentPreview(item)}>
                      {isExpanded ? 'Hide Content' : 'View Content'}
                    </button>
                    {(item.kind === 'text' || item.kind === 'chat_message') && <button className="button ghost" disabled={busy} onClick={() => editText(item)}>Edit</button>}
                    {item.kind === 'chat_message' && <button className="button ghost" onClick={() => onOpenChatMessage?.(item)}>Open Chat</button>}
                    {item.kind === 'website' && <button className="button ghost" onClick={() => desktopApi.openExternalLink(item.url)}>Open</button>}
                    {item.kind === 'website' && <button className="button ghost" disabled={busy} onClick={() => refreshWebsite(item)}>Refresh</button>}
                    {(!item.kind || item.kind === 'file') && <button className="button ghost" onClick={() => desktopApi.openPath(item.path)}>Open</button>}
                    <button className="button ghost" disabled={busy || isSyncing} onClick={() => updateEnrichment(item, !item.enrich_enabled)}>
                      {item.enrich_enabled ? 'Use Standard' : 'Enable Deep'}
                    </button>
                    <button className="button ghost" disabled={busy || isSyncing} onClick={() => removeItem(item)}>Remove</button>
                  </div>
                </article>
              )
            })}
          </div>
        ) : (
          <p className="muted-copy">{items.length ? 'No matching contents.' : 'No content added yet.'}</p>
        )}
      </div>

      <div className="library-footer-actions">
        <button className="button" disabled={busy} onClick={addPaths}>Add Files</button>
        <button className="button" disabled={busy} onClick={() => { closeForm(); setFormMode('text') }}>Add Text</button>
        <button className="button" disabled={busy} onClick={() => { closeForm(); setFormMode('website') }}>Add Website</button>
        {items.length > 0 && hasMissingMetadata && !isSyncing && isReadyForChat && (
          <button className="button ghost" disabled={busy} onClick={generateMetadata}>Generate Metadata</button>
        )}
        {items.length > 0 && !isSyncing && !isReadyForChat && (
          <button className="button ghost" disabled={busy} onClick={retrySync}>Retry Sync</button>
        )}
      </div>

      {toasts.length > 0 && (
        <div className="library-toast-stack" aria-live="polite">
          {toasts.map(toast => (
            <div key={toast.id} className={`library-toast ${toast.tone}`} role={toast.tone === 'warning' ? 'alert' : 'status'}>
              {toast.message}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
