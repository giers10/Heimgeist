import React, { useEffect, useState } from 'react'

function statusLabel(job) {
  if (!job) return null
  const type = job.type === 'prepare' ? 'prepare' : job.type
  const progress = typeof job.progress === 'number' ? `${job.progress.toFixed(0)}%` : null
  const detail = job.detail ? ` ${job.detail}` : ''
  return `${type} · ${job.status}${progress ? ` · ${progress}` : ''}${detail}`
}

export default function LibraryManager({
  apiBase,
  library,
  jobs,
  onRefresh,
  onDeleted
}) {
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')

  useEffect(() => {
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

  async function deleteLibrary() {
    if (!library) return
    await runAction(async () => {
      const response = await fetch(`${apiBase}/libraries/${library.slug}`, { method: 'DELETE' })
      await expectOk(response)
    })
    onDeleted?.(library.slug)
  }

  if (!library) {
    return (
      <div className="placeholder-view">
        <p>Create a database and add files. Heimgeist will keep its retrieval pipeline updated automatically.</p>
      </div>
    )
  }

  const activeJobs = (jobs || []).filter(job => job.slug === library.slug && (job.status === 'queued' || job.status === 'running'))
  const isSyncing = activeJobs.length > 0
  const isReadyForChat = !!library.states?.is_indexed

  return (
    <div className="library-panel">
      {confirmDelete && (
        <div className="library-inline-form danger-zone">
          <div className="muted-copy">Delete "{library.name}"? This removes the registered files and local retrieval data for this database.</div>
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
        <button
          className="button danger"
          onClick={() => {
            setConfirmDelete(true)
            setErrorMessage('')
          }}
        >
          Delete
        </button>
      </div>

      <div className="library-states">
        <div className={`state-pill ${library.states?.has_files ? 'ready' : ''}`}>Files: {library.files?.length || 0}</div>
        <div className={`state-pill ${isReadyForChat ? 'ready' : ''}`}>
          {isSyncing ? 'Syncing' : isReadyForChat ? 'Ready' : library.files?.length ? 'Needs sync' : 'No data yet'}
        </div>
      </div>

      {isSyncing && (
        <div className="library-chat-note">
          Syncing this database. Heimgeist is rebuilding the corpus, enrichment, embeddings, and indexes automatically.
        </div>
      )}

      {!library.files?.length && !isSyncing && (
        <div className="library-chat-note">
          Add files to make this database available in chat.
        </div>
      )}

      {library.files?.length > 0 && !isReadyForChat && !isSyncing && (
        <div className="library-chat-note">
          This database is waiting for its next sync.
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
