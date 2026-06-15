import { useEffect, useRef, useState } from 'react'

export default function ChatDatabasePicker({
  activeSessionId,
  chatLibrary,
  chatLibrarySlug,
  chatLibraryStatusSuffix,
  isLibrarySyncing,
  libraries,
  setChatLibraryForSession,
}) {
  const dbPickerRef = useRef(null)
  const [isDbPickerOpen, setIsDbPickerOpen] = useState(false)

  useEffect(() => {
    if (!isDbPickerOpen) return

    const onPointerDown = (event) => {
      if (!dbPickerRef.current?.contains(event.target)) {
        setIsDbPickerOpen(false)
      }
    }

    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [isDbPickerOpen])

  useEffect(() => {
    setIsDbPickerOpen(false)
  }, [activeSessionId])

  return (
    <div className="footer-tool-group" ref={dbPickerRef}>
      <button
        type="button"
        className={"db-picker-toggle" + (chatLibrary ? " active" : "")}
        onClick={() => {
          if (!activeSessionId) return
          setIsDbPickerOpen(prev => !prev)
        }}
        title={chatLibrary ? `Database: ${chatLibrary.name}${chatLibraryStatusSuffix}` : 'Select database for this chat'}
        aria-haspopup="menu"
        aria-expanded={isDbPickerOpen}
        disabled={!activeSessionId}
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
             aria-hidden="true">
          <ellipse cx="12" cy="5" rx="8" ry="3"/>
          <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/>
          <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>
        </svg>
      </button>
      {isDbPickerOpen && (
        <div className="db-picker-menu" role="menu">
          <button
            type="button"
            className={"db-picker-option" + (!chatLibrarySlug ? " selected" : "")}
            onClick={() => {
              setChatLibraryForSession(activeSessionId, null)
              setIsDbPickerOpen(false)
            }}
          >
            <span>No database</span>
            {!chatLibrarySlug && <span className="db-picker-status">Selected</span>}
          </button>
          {libraries.length === 0 ? (
            <div className="db-picker-empty">No databases yet.</div>
          ) : (
            libraries.map(library => {
              const selected = chatLibrarySlug === library.slug
              const syncing = isLibrarySyncing(library.slug)
              const status = !library.files?.length
                ? 'Empty'
                : library.states?.is_indexed
                  ? 'Ready'
                  : syncing
                    ? 'Syncing'
                    : 'Needs sync'

              return (
                <button
                  key={library.slug}
                  type="button"
                  className={"db-picker-option" + (selected ? " selected" : "")}
                  onClick={() => {
                    setChatLibraryForSession(activeSessionId, library.slug)
                    setIsDbPickerOpen(false)
                  }}
                >
                  <span>{library.name}</span>
                  <span className="db-picker-status">{selected ? 'Selected' : status}</span>
                </button>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
