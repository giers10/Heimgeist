import React, { useEffect, useState } from 'react'

function statusLabel(job) {
  if (!job) return null
  const progress = typeof job.progress === 'number' ? `${job.progress.toFixed(0)}%` : null
  const detail = job.detail ? ` ${job.detail}` : ''
  return `${job.type} · ${job.status}${progress ? ` · ${progress}` : ''}${detail}`
}

export default function LibraryManager({
  apiBase,
  library,
  jobs,
  chatLibrarySlug,
  onRefresh,
  onToggleChatLibrary,
  onDeleted
}) {
  const [busy, setBusy] = useState(false)
  const [isRenaming, setIsRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')

  useEffect(() => {
    setIsRenaming(false)
    setRenameValue(library?.name || '')
    setConfirmDelete(false)
    setErrorMessage('')
  }, [library?.slug, library?.name])

  async function expectOk(response) {
    if (response.ok) return response
    const detail = await response.text()
    throw new Error(detail || `HTTP ${response.status}`)
  }

  async function runAction(fn) {
    setBusy(true)
    try {
      setErrorMessage('')
      await fn()
      setConfirmDelete(false)
    } finally {
      setBusy(false)
      await onRefresh()
    }
  }

  async function addPaths() {
    if (!library) return
    const paths = await window.electronAPI?.pickPaths?.()
    if (!Array.isArray(paths) || paths.length === 0) return
    try {
      await runAction(async () => {
        const response = await fetch(`${apiBase}/libraries/${library.slug}/files/register`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ paths })
        })
        await expectOk(response)
      })
    } catch (error) {
      setErrorMessage(String(error?.message || error))
    }
  }

  async function removeFile(rel) {
    if (!library) return
    try {
      await runAction(async () => {
        const response = await fetch(`${apiBase}/libraries/${library.slug}/files`, {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rel })
        })
        await expectOk(response)
      })
    } catch (error) {
      setErrorMessage(String(error?.message || error))
    }
  }

  async function renameLibrary() {
    if (!library) return
    const name = renameValue.trim()
    if (!name || name === library.name) {
      setIsRenaming(false)
      setRenameValue(library.name || '')
      return
    }
    await runAction(async () => {
      const response = await fetch(`${apiBase}/libraries/${library.slug}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      })
      await expectOk(response)
    })
    setIsRenaming(false)
  }

  async function deleteLibrary() {
    if (!library) return
    await runAction(async () => {
      const response = await fetch(`${apiBase}/libraries/${library.slug}`, { method: 'DELETE' })
      await expectOk(response)
    })
    onDeleted?.(library.slug)
  }

  async function startJob(kind) {
    if (!library) return
    try {
      await runAction(async () => {
        const endpoint = `${apiBase}/libraries/${library.slug}/jobs/${kind}`
        const options = {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        }
        if (kind === 'embed') {
          options.body = JSON.stringify({})
        }
        const response = await fetch(endpoint, options)
        await expectOk(response)
      })
    } catch (error) {
      setErrorMessage(String(error?.message || error))
    }
  }

  if (!library) {
    return (
      <div className="placeholder-view">
        <p>Create a database, add files or folders, then build and index it for local RAG.</p>
      </div>
    )
  }

  const activeJobs = (jobs || []).filter(job => job.slug === library.slug && (job.status === 'queued' || job.status === 'running'))
  const usingInChat = chatLibrarySlug === library.slug
  const canUseInChat = usingInChat || !!library.states?.is_indexed
  const canStartRename = () => {
    setRenameValue(library.name || '')
    setErrorMessage('')
    setIsRenaming(true)
    setConfirmDelete(false)
  }

  return (
    <div className="library-panel">
      {isRenaming && (
        <div className="library-inline-form">
          <input
            type="text"
            className="rename-input"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                renameLibrary().catch((error) => setErrorMessage(String(error?.message || error)))
              } else if (e.key === 'Escape') {
                setIsRenaming(false)
                setRenameValue(library.name || '')
              }
            }}
            autoFocus
          />
          <div className="new-db-actions">
            <button
              className="button"
              disabled={busy}
              onClick={() => renameLibrary().catch((error) => setErrorMessage(String(error?.message || error)))}
            >
              Save
            </button>
            <button
              className="button ghost"
              onClick={() => {
                setIsRenaming(false)
                setRenameValue(library.name || '')
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {confirmDelete && (
        <div className="library-inline-form danger-zone">
          <div className="muted-copy">Delete "{library.name}"? This removes the local index and metadata for this database.</div>
          <div className="new-db-actions">
            <button
              className="button danger"
              disabled={busy}
              onClick={() => deleteLibrary().catch((error) => setErrorMessage(String(error?.message || error)))}
            >
              Confirm Delete
            </button>
            <button className="button ghost" onClick={() => setConfirmDelete(false)}>Cancel</button>
          </div>
        </div>
      )}

      {errorMessage && <div className="form-error">{errorMessage}</div>}

      <div className="library-toolbar">
        <button className="button" disabled={busy} onClick={addPaths}>Add Files</button>
        <button className="button" disabled={busy || !library.files?.length} onClick={() => startJob('build')}>Build Corpus</button>
        <button className="button" disabled={busy || !library.states?.has_corpus} onClick={() => startJob('enrich')}>Enrich</button>
        <button className="button" disabled={busy || !library.states?.has_corpus} onClick={() => startJob('embed')}>Index</button>
        <button className="button" onClick={canStartRename}>Rename</button>
        <button
          className="button"
          disabled={!canUseInChat}
          title={!canUseInChat ? 'Index this database before using it in chat.' : ''}
          onClick={() => onToggleChatLibrary(usingInChat ? null : library.slug)}
        >
          {usingInChat ? 'Stop Using In Chat' : 'Use In Chat'}
        </button>
        <button
          className="button danger"
          onClick={() => {
            setConfirmDelete(true)
            setIsRenaming(false)
            setErrorMessage('')
          }}
        >
          Delete
        </button>
      </div>

      <div className="library-states">
        <div className={`state-pill ${library.states?.has_files ? 'ready' : ''}`}>Files: {library.files?.length || 0}</div>
        <div className={`state-pill ${library.states?.has_corpus ? 'ready' : ''}`}>Corpus: {library.artifacts?.corpus_records || 0}</div>
        <div className={`state-pill ${library.states?.is_enriched ? 'ready' : ''}`}>Enriched: {library.artifacts?.enhanced_records || 0}</div>
        <div className={`state-pill ${library.states?.is_indexed ? 'ready' : ''}`}>Indexed</div>
      </div>

      {usingInChat && (
        <div className="library-chat-note">
          This database will be queried before each chat request and its context will be appended to the prompt.
        </div>
      )}

      {!library.states?.is_indexed && !usingInChat && (
        <div className="library-chat-note">
          Run Index before using this database in chat.
        </div>
      )}

      {activeJobs.length > 0 && (
        <div className="library-jobs">
          {activeJobs.map(job => (
            <div key={job.id} className={`job-card ${job.status}`}>
              {statusLabel(job)}
            </div>
          ))}
        </div>
      )}

      <div className="library-files">
        <h2>Files</h2>
        {library.files?.length ? (
          <div className="library-file-list">
            {library.files.map(file => (
              <div key={file.sha256 || file.rel} className="library-file-row">
                <div className="library-file-meta">
                  <div className="library-file-name">{file.name || file.path}</div>
                  <div className="library-file-path">{file.path}</div>
                </div>
                <div className="library-file-actions">
                  <button className="button ghost" onClick={() => window.electronAPI?.openPath?.(file.path)}>Open</button>
                  <button className="button ghost" onClick={() => removeFile(file.rel)}>Remove</button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted-copy">No files registered yet.</p>
        )}
      </div>
    </div>
  )
}
