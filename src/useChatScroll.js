import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { BOTTOM_EPSILON, TOP_ALIGN_OFFSET } from './appConfig'

export function useChatScroll({
  activeSessionId,
  activeSidebarMode,
  chatRef,
  messagesLength,
}) {
  const [scrollPositions, setScrollPositions] = useState({})
  const [userScrolledUpState, setUserScrolledUpState] = useState({})
  const userScrolledUpRef = useRef({})
  const [pendingScrollToLastUser, setPendingScrollToLastUser] = useState({})
  const scrollTopsRef = useRef({})
  const prevScrollTopsRef = useRef({})
  const [newMsgTip, setNewMsgTip] = useState({})
  const restoredForRef = useRef(null)
  const activeSessionIdRef = useRef(activeSessionId)

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId
  }, [activeSessionId])

  const setUserScrolledUp = useCallback((sessionId, value) => {
    setUserScrolledUpState(prev => {
      const next = { ...prev, [sessionId]: value }
      userScrolledUpRef.current = next
      return next
    })
  }, [])

  useEffect(() => {
    const leavingSessionId = activeSessionId
    const leavingMode = activeSidebarMode

    return () => {
      if (leavingMode === 'chats' && leavingSessionId) {
        const top = typeof scrollTopsRef.current[leavingSessionId] === 'number'
          ? scrollTopsRef.current[leavingSessionId]
          : (chatRef.current ? chatRef.current.scrollTop : 0)

        setScrollPositions(prev => {
          const updated = { ...prev, [leavingSessionId]: top }
          window.electronAPI.updateSettings({ scrollPositions: updated })
          return updated
        })
      }
    }
  }, [activeSessionId, activeSidebarMode, chatRef])

  useEffect(() => {
    const chatDiv = chatRef.current
    if (!chatDiv) return

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = chatDiv
      const isAtBottom = (scrollHeight - scrollTop - clientHeight) <= BOTTOM_EPSILON

      if (activeSessionId) {
        const prevScrollTop = prevScrollTopsRef.current[activeSessionId]
        const scrolledUp = typeof prevScrollTop === 'number' && scrollTop < prevScrollTop

        scrollTopsRef.current[activeSessionId] = scrollTop

        if (isAtBottom) {
          setUserScrolledUp(activeSessionId, false)
        } else if (scrolledUp) {
          setUserScrolledUp(activeSessionId, true)
        }
        prevScrollTopsRef.current[activeSessionId] = scrollTop
      }
    }

    chatDiv.addEventListener('scroll', handleScroll)
    return () => chatDiv.removeEventListener('scroll', handleScroll)
  }, [activeSessionId, chatRef, setUserScrolledUp])

  useEffect(() => {
    const sid = activeSessionId
    if (!sid) return
    if (userScrolledUpState[sid] === false) {
      setNewMsgTip(prev => {
        if (!(sid in prev)) return prev
        const rest = { ...prev }
        delete rest[sid]
        return rest
      })
    }
  }, [activeSessionId, userScrolledUpState])

  useLayoutEffect(() => {
    if (activeSidebarMode !== 'chats' || !activeSessionId) return

    const div = chatRef.current
    if (!div) return

    restoredForRef.current = null

    const applyRestore = () => {
      if (restoredForRef.current === activeSessionId) return

      const liveSaved = typeof scrollTopsRef.current[activeSessionId] === 'number'
        ? scrollTopsRef.current[activeSessionId]
        : undefined
      const saved = typeof liveSaved === 'number'
        ? liveSaved
        : scrollPositions[activeSessionId]

      if (typeof saved === 'number') {
        div.scrollTop = saved
        restoredForRef.current = activeSessionId
        return
      }
      if (messagesLength > 0) {
        div.scrollTop = div.scrollHeight
        restoredForRef.current = activeSessionId
      }
    }

    applyRestore()
    const r0 = requestAnimationFrame(applyRestore)

    const onDomChange = () => {
      if (restoredForRef.current !== activeSessionId) {
        requestAnimationFrame(applyRestore)
      }
    }

    const mo = new MutationObserver(onDomChange)
    mo.observe(div, { childList: true, subtree: true })

    const ro = new ResizeObserver(onDomChange)
    ro.observe(div)

    return () => {
      cancelAnimationFrame(r0)
      mo.disconnect()
      ro.disconnect()
    }
  }, [activeSessionId, activeSidebarMode, chatRef, messagesLength, scrollPositions])

  useEffect(() => {
    if (activeSidebarMode !== 'chats' || !activeSessionId) return
    if (restoredForRef.current === activeSessionId) return

    const liveSaved = typeof scrollTopsRef.current[activeSessionId] === 'number'
      ? scrollTopsRef.current[activeSessionId]
      : undefined
    const savedScrollTop = typeof liveSaved === 'number'
      ? liveSaved
      : scrollPositions[activeSessionId]

    if (typeof savedScrollTop !== 'number' && messagesLength > 0) {
      requestAnimationFrame(() => {
        const div = chatRef.current
        if (!div) return
        div.scrollTop = div.scrollHeight
        restoredForRef.current = activeSessionId
      })
    }
  }, [messagesLength, activeSessionId, activeSidebarMode, chatRef, scrollPositions])

  const scrollToBottom = useCallback((behavior = 'smooth', sessionId = null) => {
    const chatDiv = chatRef.current
    if (!chatDiv) return
    const target = sessionId ?? activeSessionIdRef.current
    if (activeSessionIdRef.current !== target) return
    chatDiv.scrollTo({ top: chatDiv.scrollHeight, behavior })
    setUserScrolledUp(target, false)
  }, [chatRef, setUserScrolledUp])

  const scrollMessageToTop = useCallback((msgId, behavior = 'auto', sessionId = null) => {
    const chatDiv = chatRef.current
    if (!chatDiv) return
    const target = sessionId ?? activeSessionIdRef.current
    if (activeSessionIdRef.current !== target) return
    const el = document.getElementById(msgId)
    if (el) {
      const top = Math.max(0, el.offsetTop - TOP_ALIGN_OFFSET)
      chatDiv.scrollTo({ top, behavior })
    }
  }, [chatRef])

  const handleNewMsgTipClick = useCallback(() => {
    const sid = activeSessionIdRef.current
    const msgId = newMsgTip[sid]
    if (msgId) {
      scrollMessageToTop(msgId, 'smooth', sid)
      setNewMsgTip(prev => {
        const { [sid]: _omit, ...rest } = prev
        return rest
      })
    }
  }, [newMsgTip, scrollMessageToTop])

  const scrollPendingMessageForSession = useCallback((sessionId, chatSessions) => {
    const pendingId = pendingScrollToLastUser[sessionId]
    if (!pendingId) return

    requestAnimationFrame(() => {
      let tries = 12
      const attempt = () => {
        const chatDiv = chatRef.current
        if (!chatDiv) return

        let el = document.getElementById(pendingId)
        if (!el) {
          const sess = chatSessions.find(s => s.session_id === sessionId)
          if (sess && Array.isArray(sess.messages)) {
            for (let i = sess.messages.length - 1; i >= 0; i--) {
              const m = sess.messages[i]
              if (m.role === 'assistant' && m.id) {
                el = document.getElementById(m.id)
                break
              }
            }
          }
        }

        if (el) {
          scrollMessageToTop(el.id, 'smooth', sessionId)
          setPendingScrollToLastUser(prev => {
            const { [sessionId]: _omit, ...rest } = prev
            return rest
          })
        } else if (tries-- > 0) {
          requestAnimationFrame(attempt)
        }
      }
      requestAnimationFrame(attempt)
    })
  }, [chatRef, pendingScrollToLastUser, scrollMessageToTop])

  return {
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
  }
}
