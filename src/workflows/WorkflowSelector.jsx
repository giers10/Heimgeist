import React from 'react'

export default function WorkflowSelector({ disabled, onChange, selection, workflows }) {
  const value = selection.mode === 'explicit' ? `workflow:${selection.workflowId}` : selection.mode
  return (
    <label className="workflow-selector" title="Choose how this chat prompt is executed">
      <span>Flow</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => {
          const next = event.target.value
          if (next === 'direct' || next === 'automatic') onChange({ mode: next, workflowId: null })
          else onChange({ mode: 'explicit', workflowId: next.replace(/^workflow:/, '') })
        }}
      >
        <option value="direct">Direct</option>
        <option value="automatic">Automatic</option>
        {workflows.filter(item => item.enabled && item.slug !== 'input-output').map(item => (
          <option key={item.id} value={`workflow:${item.id}`}>{item.name}</option>
        ))}
      </select>
    </label>
  )
}
