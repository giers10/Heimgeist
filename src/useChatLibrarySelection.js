import { useEffect, useMemo, useState } from 'react'
import { CHAT_LIBRARY_MAP_KEY } from './appConfig'

function libraryJobIsActive(job) {
  return job?.status === 'queued' || job?.status === 'running'
}

function getLibraryStatusSuffix(library, hasActiveJob) {
  if (!library) return ''
  if (!library.files?.length) return ' (empty)'
  if (library.states?.is_indexed) return ''
  return hasActiveJob ? ' (syncing)' : ' (needs sync)'
}

function loadChatLibraryMap() {
  try {
    const raw = localStorage.getItem(CHAT_LIBRARY_MAP_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

export function useChatLibrarySelection({ activeSessionId, libraries, libraryJobs }) {
  const [chatLibraryBySession, setChatLibraryBySession] = useState(loadChatLibraryMap)

  useEffect(() => {
    try {
      localStorage.setItem(CHAT_LIBRARY_MAP_KEY, JSON.stringify(chatLibraryBySession || {}))
    } catch {}
  }, [chatLibraryBySession])

  useEffect(() => {
    const validSlugs = new Set(libraries.map(library => library.slug))
    setChatLibraryBySession(prev => {
      let changed = false
      const next = {}
      for (const [sessionId, slug] of Object.entries(prev || {})) {
        if (validSlugs.has(slug)) {
          next[sessionId] = slug
        } else {
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [libraries])

  const chatLibrarySlug = activeSessionId ? (chatLibraryBySession[activeSessionId] || null) : null

  const chatLibrary = useMemo(() => {
    return libraries.find(lib => lib.slug === chatLibrarySlug) || null
  }, [chatLibrarySlug, libraries])

  const chatLibraryHasActiveJob = useMemo(() => {
    if (!chatLibrarySlug) return false
    return libraryJobs.some(job => job.slug === chatLibrarySlug && libraryJobIsActive(job))
  }, [chatLibrarySlug, libraryJobs])

  const chatLibraryStatusSuffix = useMemo(() => {
    return getLibraryStatusSuffix(chatLibrary, chatLibraryHasActiveJob)
  }, [chatLibrary, chatLibraryHasActiveJob])

  function getChatLibrarySlugForSession(sessionId) {
    if (!sessionId) return null
    return chatLibraryBySession[sessionId] || null
  }

  function getChatLibraryForSession(sessionId) {
    const slug = getChatLibrarySlugForSession(sessionId)
    if (!slug) return null
    return libraries.find(lib => lib.slug === slug) || null
  }

  function isLibrarySyncing(slug) {
    if (!slug) return false
    return libraryJobs.some(job => job.slug === slug && libraryJobIsActive(job))
  }

  function setChatLibraryForSession(sessionId, slug) {
    if (!sessionId) return
    setChatLibraryBySession(prev => {
      const next = { ...(prev || {}) }
      if (slug) {
        next[sessionId] = slug
      } else {
        delete next[sessionId]
      }
      return next
    })
  }

  function removeLibraryFromChatSelections(slug) {
    if (!slug) return
    setChatLibraryBySession(prev => {
      let changed = false
      const next = {}
      for (const [sessionId, librarySlug] of Object.entries(prev || {})) {
        if (librarySlug === slug) {
          changed = true
          continue
        }
        next[sessionId] = librarySlug
      }
      return changed ? next : prev
    })
  }

  function clearChatLibrarySelections() {
    setChatLibraryBySession({})
  }

  return {
    chatLibrary,
    chatLibraryBySession,
    chatLibrarySlug,
    chatLibraryStatusSuffix,
    clearChatLibrarySelections,
    getChatLibraryForSession,
    isLibrarySyncing,
    removeLibraryFromChatSelections,
    setChatLibraryForSession,
  }
}
