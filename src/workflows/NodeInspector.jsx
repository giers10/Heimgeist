import React from 'react'

const CONDITION_OPERATIONS = ['equals', 'not_equals', 'exists', 'greater_than', 'less_than', 'contains', 'array_not_empty', 'score_at_least']

export default function NodeInspector({ node, tools, onChange, onDelete, readOnly }) {
  const [configText, setConfigText] = React.useState('{}')
  const [configError, setConfigError] = React.useState('')

  React.useEffect(() => {
    setConfigText(JSON.stringify(node?.data?.config || {}, null, 2))
    setConfigError('')
  }, [node])

  if (!node) return <div className="workflow-inspector empty">Select a node to edit it.</div>
  const nodeType = node.data.nodeType
  const patchConfig = (config) => onChange({ ...node, data: { ...node.data, config, tool: config.tool || '' } })
  return (
    <div className="workflow-inspector">
      <div className="workflow-inspector-heading">
        <strong>{node.id}</strong>
        {!readOnly && <button type="button" className="button danger ghost" onClick={onDelete}>Delete</button>}
      </div>
      <label>Label
        <input disabled={readOnly} value={node.data.label || ''} onChange={(event) => onChange({ ...node, data: { ...node.data, label: event.target.value } })} />
      </label>
      {nodeType === 'tool' && (
        <label>Registered tool
          <select
            disabled={readOnly}
            value={node.data.config?.tool || ''}
            onChange={(event) => patchConfig({ ...node.data.config, tool: event.target.value, arguments: node.data.config?.arguments || {} })}
          >
            <option value="">Select a tool</option>
            {tools.map(tool => <option key={tool.name} value={tool.name}>{tool.name}</option>)}
          </select>
        </label>
      )}
      {nodeType === 'condition' && (
        <label>Operation
          <select disabled={readOnly} value={node.data.config?.operation || 'equals'} onChange={(event) => patchConfig({ ...node.data.config, operation: event.target.value })}>
            {CONDITION_OPERATIONS.map(operation => <option key={operation}>{operation}</option>)}
          </select>
        </label>
      )}
      <label>Configuration JSON
        <textarea
          rows={18}
          disabled={readOnly}
          value={configText}
          onChange={(event) => {
            const text = event.target.value
            setConfigText(text)
            try {
              patchConfig(JSON.parse(text))
              setConfigError('')
            } catch (error) {
              setConfigError(error.message)
            }
          }}
        />
      </label>
      {configError && <p className="workflow-validation-error">{configError}</p>}
    </div>
  )
}
