import React from 'react'
import NodePalette from './NodePalette'
import NodeInspector from './NodeInspector'
import WorkflowCanvas from './WorkflowCanvas'
import WorkflowRunInspector from './WorkflowRunInspector'
import WorkflowValidationPanel from './WorkflowValidationPanel'
import {
  createWorkflowRun,
  getWorkflowRun,
  saveWorkflowRevision,
  streamWorkflowEvents,
  updateWorkflow,
  validateWorkflow,
} from './workflowApi'

function toCanvasNode(node) {
  return {
    id: node.id,
    type: 'workflow',
    position: node.position || { x: 0, y: 0 },
    data: {
      nodeType: node.type,
      label: node.config?.label || node.id,
      config: node.config || {},
      tool: node.config?.tool || '',
    },
  }
}

function toGraphNode(node) {
  const config = { ...(node.data.config || {}) }
  if (node.data.label && node.data.label !== node.id) config.label = node.data.label
  return { id: node.id, type: node.data.nodeType, position: node.position, config }
}

function nextNodeConfig(type) {
  if (type === 'tool') return { tool: '', arguments: {} }
  if (type === 'prompt') return { model_source: 'chat_model', system_template: '', user_template: '{{input.prompt}}', output_mode: 'text', temperature: 0.2 }
  if (type === 'agent') return { model: { $ref: 'run.chat_model' }, messages: { $ref: 'run.messages' }, allowed_tool_names: [], maximum_rounds: 4 }
  if (type === 'condition') return { value: { $ref: 'input.prompt' }, operation: 'exists', compare_to: null }
  if (type === 'merge') return { values: [], deduplicate_by: 'url' }
  if (type === 'template') return { template: '{{input.prompt}}', values: {} }
  if (type === 'select') return { value: { $ref: 'input.prompt' } }
  if (type === 'limit') return { value: { $ref: 'input.prompt' }, maximum_chars: 12000 }
  if (type === 'output') return { value: { content: { $ref: 'input.prompt' }, sources: [], usage: {} } }
  return {}
}

export default function WorkflowEditor({ apiBase, model, onChanged, tools, workflow }) {
  const [nodes, setNodes] = React.useState([])
  const [edges, setEdges] = React.useState([])
  const [selectedNodeId, setSelectedNodeId] = React.useState(null)
  const [inputsText, setInputsText] = React.useState('{}')
  const [outputsText, setOutputsText] = React.useState('{}')
  const [limits, setLimits] = React.useState({})
  const [metadata, setMetadata] = React.useState({ name: '', description: '', routing_description: '', routing_examples: [], enabled: true })
  const [validation, setValidation] = React.useState(null)
  const [status, setStatus] = React.useState('')
  const [testPrompt, setTestPrompt] = React.useState('Summarize this sample request.')
  const [testRun, setTestRun] = React.useState(null)
  const [testEvents, setTestEvents] = React.useState([])

  React.useEffect(() => {
    const graph = workflow?.graph || {}
    setNodes((graph.nodes || []).map(toCanvasNode))
    setEdges(graph.edges || [])
    setInputsText(JSON.stringify(graph.inputs || {}, null, 2))
    setOutputsText(JSON.stringify(graph.outputs || {}, null, 2))
    setLimits(graph.limits || {})
    setMetadata({
      name: workflow?.name || '', description: workflow?.description || '',
      routing_description: workflow?.routing_description || '', routing_examples: workflow?.routing_examples || [],
      enabled: workflow?.enabled !== false,
    })
    setSelectedNodeId(null)
    setValidation(null)
    setTestRun(null)
    setTestEvents([])
  }, [workflow])

  const readOnly = Boolean(workflow?.built_in)
  const selectedNode = nodes.find(node => node.id === selectedNodeId) || null
  const buildGraph = () => ({
    schema_version: 1,
    inputs: JSON.parse(inputsText || '{}'),
    outputs: JSON.parse(outputsText || '{}'),
    nodes: nodes.map(toGraphNode),
    edges: edges.map(edge => ({
      id: edge.id, source: edge.source, source_handle: edge.sourceHandle || edge.source_handle || 'output',
      target: edge.target, target_handle: edge.targetHandle || edge.target_handle || 'input',
    })),
    limits,
  })

  const addNode = (type, position = { x: 120 + nodes.length * 35, y: 120 + nodes.length * 20 }) => {
    if (readOnly) return
    const id = `${type}-${Date.now().toString(36)}`
    setNodes(previous => [...previous, toCanvasNode({ id, type, position, config: nextNodeConfig(type) })])
    setSelectedNodeId(id)
  }

  const runValidation = async () => {
    try {
      const result = await validateWorkflow(apiBase, buildGraph())
      setValidation(result)
      return result
    } catch (error) {
      setValidation({ valid: false, errors: [{ code: 'editor', message: error.message }] })
      return null
    }
  }

  const save = async () => {
    const result = await runValidation()
    if (!result?.valid) return
    setStatus('Saving…')
    try {
      await updateWorkflow(apiBase, workflow.id, metadata)
      await saveWorkflowRevision(apiBase, workflow.id, buildGraph())
      setStatus('Saved as a new revision.')
      await onChanged(workflow.id)
    } catch (error) {
      setStatus(error.message)
    }
  }

  const test = async () => {
    const result = await runValidation()
    if (!result?.valid) return
    setTestRun({ status: 'starting', node_runs: [] })
    setTestEvents([])
    try {
      const created = await createWorkflowRun(apiBase, {
        message: testPrompt, model, selection_mode: 'explicit', workflow_id: workflow.id,
        sample_inputs: { prompt: testPrompt }, stream: true, explicit_user_action: true,
      })
      await streamWorkflowEvents(apiBase, created.run_id, {
        onEvent: event => setTestEvents(previous => [...previous, event]),
      })
      setTestRun(await getWorkflowRun(apiBase, created.run_id))
    } catch (error) {
      setTestRun({ status: 'failed', error: { message: error.message }, node_runs: [] })
    }
  }

  if (!workflow) return <div className="workflow-empty">Select a workflow.</div>
  return (
    <div className="workflow-editor">
      <div className="workflow-editor-toolbar">
        <input value={metadata.name} disabled={readOnly} onChange={(event) => setMetadata(value => ({ ...value, name: event.target.value }))} />
        <button className="button ghost" type="button" onClick={runValidation}>Validate</button>
        {!readOnly && <button className="button" type="button" onClick={save}>Save revision</button>}
        {readOnly && <span className="header-subtle">Built-in revisions are read-only.</span>}
        {status && <span className="header-subtle">{status}</span>}
      </div>
      <div className="workflow-editor-meta">
        <label>Description<textarea disabled={readOnly} value={metadata.description} onChange={(event) => setMetadata(value => ({ ...value, description: event.target.value }))} /></label>
        <label>Routing description<textarea disabled={readOnly} value={metadata.routing_description} onChange={(event) => setMetadata(value => ({ ...value, routing_description: event.target.value }))} /></label>
        <label>Routing examples<input disabled={readOnly} value={metadata.routing_examples.join(' | ')} onChange={(event) => setMetadata(value => ({ ...value, routing_examples: event.target.value.split('|').map(item => item.trim()).filter(Boolean) }))} /></label>
        <label className="workflow-enabled"><input type="checkbox" disabled={readOnly} checked={metadata.enabled} onChange={(event) => setMetadata(value => ({ ...value, enabled: event.target.checked }))} /> Enabled for routing</label>
      </div>
      <div className="workflow-editor-grid">
        <NodePalette onAdd={addNode} />
        <WorkflowCanvas nodes={nodes} edges={edges} onNodesChange={setNodes} onEdgesChange={setEdges} onSelectNode={setSelectedNodeId} onDropNode={addNode} />
        <NodeInspector
          node={selectedNode}
          tools={tools}
          readOnly={readOnly}
          onChange={(next) => setNodes(previous => previous.map(node => node.id === next.id ? next : node))}
          onDelete={() => {
            setNodes(previous => previous.filter(node => node.id !== selectedNodeId))
            setEdges(previous => previous.filter(edge => edge.source !== selectedNodeId && edge.target !== selectedNodeId))
            setSelectedNodeId(null)
          }}
        />
      </div>
      <details className="workflow-schema-editor">
        <summary>Workflow input/output schemas and limits</summary>
        <div>
          <label>Inputs JSON<textarea disabled={readOnly} rows={10} value={inputsText} onChange={(event) => setInputsText(event.target.value)} /></label>
          <label>Outputs JSON<textarea disabled={readOnly} rows={10} value={outputsText} onChange={(event) => setOutputsText(event.target.value)} /></label>
          <label>Limits JSON<textarea disabled={readOnly} rows={10} value={JSON.stringify(limits, null, 2)} onChange={(event) => { try { setLimits(JSON.parse(event.target.value)) } catch {} }} /></label>
        </div>
      </details>
      <WorkflowValidationPanel result={validation} />
      <div className="workflow-test-panel">
        <input value={testPrompt} onChange={(event) => setTestPrompt(event.target.value)} />
        <button className="button ghost" type="button" onClick={test} disabled={!model}>Run test</button>
      </div>
      <WorkflowRunInspector run={testRun} events={testEvents} />
    </div>
  )
}
