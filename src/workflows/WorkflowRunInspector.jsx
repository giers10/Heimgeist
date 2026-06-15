import React from 'react'

export default function WorkflowRunInspector({ run, events }) {
  if (!run && !(events || []).length) return null
  return (
    <div className="workflow-run-inspector">
      <strong>Test run {run?.status || 'running'}</strong>
      {(run?.node_runs || []).map(node => (
        <details key={node.id || node.node_id}>
          <summary>{node.node_id}: {node.status} {node.metrics?.duration_seconds != null ? `(${node.metrics.duration_seconds.toFixed(2)}s)` : ''}</summary>
          {node.resolved_inputs && <pre>{JSON.stringify(node.resolved_inputs, null, 2)}</pre>}
          {node.outputs && <pre>{JSON.stringify(node.outputs, null, 2)}</pre>}
          {node.error && <pre className="workflow-validation-error">{JSON.stringify(node.error, null, 2)}</pre>}
        </details>
      ))}
      {run?.error && <pre className="workflow-validation-error">{JSON.stringify(run.error, null, 2)}</pre>}
    </div>
  )
}
