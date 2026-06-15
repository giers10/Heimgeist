import React from 'react'
import { markdownToHTML } from './markdown'
import { enrichOllamaErrorText, splitThinkBlocks } from './chatText'
import desktopApi from './desktop/desktopApi'

export default function AssistantMessageContent({ content, streamOutput, sources }) {
  const displayContent = enrichOllamaErrorText(content || '')
  const { think, answer } = splitThinkBlocks(displayContent)
  const [open, setOpen] = React.useState(false)
  const showThinkButton = !!think

  return (
    <div className="assistant-message">
      {showThinkButton && (
        <div className="assistant-thoughts">
          <button
            className="think-toggle"
            onClick={() => setOpen(o => !o)}
            aria-expanded={open ? 'true' : 'false'}
            aria-controls="think-content"
          >
            <span className="think-toggle-icon" aria-hidden="true">
              {open ? '▾' : '▸'}
            </span>
            Thoughts
          </button>
          {open && (
            <div
              id="think-content"
              className="think-content"
              dangerouslySetInnerHTML={{ __html: markdownToHTML(think) }}
            />
          )}
        </div>
      )}
      <div
        className="msg-content"
        dangerouslySetInnerHTML={{ __html: markdownToHTML(answer || displayContent || '') }}
      />
      {Array.isArray(sources) && sources.length > 0 && (
        <div className="msg-sources chips">
          {sources.map((source, i) => {
            const u = typeof source === 'string' ? source : (source?.url || source?.canonical_url || '')
            let label = typeof source === 'object' && source?.title ? source.title : u
            let isFile = false
            try {
              const parsed = new URL(u)
              if (parsed.protocol === 'file:') {
                isFile = true
                const parts = parsed.pathname.split('/').filter(Boolean)
                label = decodeURIComponent(parts[parts.length - 1] || u)
              } else {
                const host = parsed.hostname || u
                label = host.replace(/^www\./i, '')
              }
            } catch {}
            return (
              <a
                key={(u || JSON.stringify(source)) + i}
                className="chip"
                href={u || undefined}
                target="_blank"
                rel="noreferrer"
                title={typeof source === 'object' ? (source.snippet || u) : u}
                onClick={(event) => {
                  if (!isFile || !u) return
                  event.preventDefault()
                  try {
                    const parsed = new URL(u)
                    desktopApi.openPath(decodeURIComponent(parsed.pathname))
                  } catch {}
                }}
              >
                {label}
              </a>
            )
          })}
        </div>
      )}
    </div>
  )
}
