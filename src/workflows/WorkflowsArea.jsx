import React from 'react'
import WorkflowEditor from './WorkflowEditor'

export default function WorkflowsArea({ apiBase, error, model, onChanged, tools, workflow }) {
  return (
    <div className="workflows-area">
      <div className="workflows-main">
        {error && <div className="workflow-validation-error">{error}</div>}
        <WorkflowEditor
          apiBase={apiBase}
          model={model}
          onChanged={onChanged}
          tools={tools}
          workflow={workflow}
        />
      </div>
    </div>
  )
}
