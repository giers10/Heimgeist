import React from 'react'

export default function WorkflowList({ activeId, onCreate, onDelete, onDuplicate, onSelect, workflows }) {
  return (
    <div className="workflow-list">
      <div className="workflow-list-heading">
        <strong>Workflows</strong>
        <button className="button" type="button" onClick={onCreate}>New</button>
      </div>
      {workflows.map(workflow => (
        <div key={workflow.id} className={`workflow-list-item${workflow.id === activeId ? ' active' : ''}`} onClick={() => onSelect(workflow.id)}>
          <div>
            <strong>{workflow.name}</strong>
            <small>{workflow.built_in ? 'Built-in' : `Revision ${workflow.current_version || 1}`}{workflow.enabled ? '' : ' · Disabled'}</small>
          </div>
          <div className="workflow-list-actions">
            <button type="button" className="icon-button" title="Duplicate" onClick={(event) => { event.stopPropagation(); onDuplicate(workflow.id) }}>⧉</button>
            {!workflow.built_in && <button type="button" className="icon-button" title="Delete" onClick={(event) => { event.stopPropagation(); onDelete(workflow.id) }}>×</button>}
          </div>
        </div>
      ))}
    </div>
  )
}
