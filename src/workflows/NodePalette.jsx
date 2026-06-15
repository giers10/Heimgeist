import React from 'react'

export const NODE_TYPES = ['input', 'output', 'tool', 'prompt', 'agent', 'condition', 'merge', 'template', 'select', 'limit']

export default function NodePalette({ onAdd }) {
  return (
    <div className="workflow-palette">
      <strong>Nodes</strong>
      {NODE_TYPES.map(type => (
        <button
          key={type}
          type="button"
          draggable
          onDragStart={(event) => event.dataTransfer.setData('application/heimgeist-workflow-node', type)}
          onClick={() => onAdd(type)}
        >
          {type}
        </button>
      ))}
    </div>
  )
}
