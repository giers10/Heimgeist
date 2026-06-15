import React from 'react'

const ACTIVE_TYPES = new Set(['node_started', 'tool_started', 'model_token', 'confirmation_required'])

export default function WorkflowExecutionPanel({ execution }) {
  const [open, setOpen] = React.useState(false)
  if (!execution) return null
  const events = execution.events || []
  const current = [...events].reverse().find(event => ACTIVE_TYPES.has(event.type))
  const duration = execution.startedAt
    ? Math.max(0, ((execution.finishedAt || Date.now()) - execution.startedAt) / 1000).toFixed(1)
    : '0.0'
  return (
    <div className={`workflow-execution ${execution.status || ''}`}>
      <button type="button" className="workflow-execution-summary" onClick={() => setOpen(value => !value)}>
        <span>{open ? '▾' : '▸'}</span>
        <strong>{execution.workflowName || 'Workflow'}</strong>
        <span>{execution.status || 'running'}</span>
        <span>{duration}s</span>
      </button>
      {open && (
        <div className="workflow-execution-body">
          {current && <div className="workflow-current-node">Current: {current.node_id || current.payload?.tool || current.type}</div>}
          <div className="workflow-event-list">
            {events.filter(event => event.type !== 'model_token').slice(-40).map(event => (
              <div key={event.sequence} className={`workflow-event ${event.type}`}>
                <span>{event.type.replaceAll('_', ' ')}</span>
                <code>{event.node_id || event.payload?.tool || ''}</code>
                {event.payload?.error && <small>{String(event.payload.error)}</small>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
