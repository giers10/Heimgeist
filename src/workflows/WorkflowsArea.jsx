import React from 'react'
import WorkflowEditor from './WorkflowEditor'
import WorkflowList from './WorkflowList'
import {
  createWorkflow,
  deleteWorkflow,
  duplicateWorkflow,
  fetchTools,
  fetchWorkflow,
  fetchWorkflows,
} from './workflowApi'

const DEFAULT_GRAPH = {
  schema_version: 1,
  inputs: { prompt: { type: 'string' }, session_id: { type: ['string', 'null'] } },
  outputs: { content: { type: 'string' } },
  nodes: [
    { id: 'input', type: 'input', position: { x: 80, y: 120 }, config: {} },
    { id: 'output', type: 'output', position: { x: 440, y: 120 }, config: { value: { content: { $ref: 'input.prompt' }, sources: [], usage: {} } } },
  ],
  edges: [{ id: 'input-output', source: 'input', source_handle: 'output', target: 'output', target_handle: 'input' }],
  limits: { maximum_nodes: 40, maximum_tool_calls: 20, maximum_llm_calls: 8, maximum_runtime_seconds: 300, maximum_result_chars: 120000, maximum_concurrency: 4 },
}

export default function WorkflowsArea({ apiBase, model, onWorkflowsChanged }) {
  const [workflows, setWorkflows] = React.useState([])
  const [tools, setTools] = React.useState([])
  const [activeId, setActiveId] = React.useState(null)
  const [activeWorkflow, setActiveWorkflow] = React.useState(null)
  const [error, setError] = React.useState('')

  const refresh = React.useCallback(async (preferredId = null) => {
    try {
      const [workflowData, toolData] = await Promise.all([fetchWorkflows(apiBase), fetchTools(apiBase)])
      const next = workflowData.workflows || []
      setWorkflows(next)
      setTools(toolData.tools || [])
      const nextId = preferredId || activeId || next[0]?.id || null
      setActiveId(nextId)
      setActiveWorkflow(nextId ? await fetchWorkflow(apiBase, nextId) : null)
      onWorkflowsChanged?.(next)
      setError('')
    } catch (nextError) {
      setError(nextError.message)
    }
  }, [activeId, apiBase, onWorkflowsChanged])

  React.useEffect(() => { refresh() }, [apiBase])

  const select = async (id) => {
    setActiveId(id)
    try { setActiveWorkflow(await fetchWorkflow(apiBase, id)) } catch (nextError) { setError(nextError.message) }
  }

  return (
    <div className="workflows-area">
      <WorkflowList
        workflows={workflows}
        activeId={activeId}
        onSelect={select}
        onCreate={async () => {
          const created = await createWorkflow(apiBase, { name: 'Untitled Workflow', graph: DEFAULT_GRAPH })
          await refresh(created.id)
        }}
        onDuplicate={async (id) => { const created = await duplicateWorkflow(apiBase, id); await refresh(created.id) }}
        onDelete={async (id) => {
          if (!window.confirm('Delete this workflow and its revisions?')) return
          await deleteWorkflow(apiBase, id)
          await refresh()
        }}
      />
      <div className="workflows-main">
        {error && <div className="workflow-validation-error">{error}</div>}
        <WorkflowEditor apiBase={apiBase} model={model} tools={tools} workflow={activeWorkflow} onChanged={refresh} />
      </div>
    </div>
  )
}
