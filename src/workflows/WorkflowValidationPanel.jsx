import React from 'react'

export default function WorkflowValidationPanel({ result }) {
  if (!result) return null
  return (
    <div className={`workflow-validation ${result.valid ? 'valid' : 'invalid'}`}>
      <strong>{result.valid ? 'Workflow is valid' : `${result.errors?.length || 0} validation error(s)`}</strong>
      {(result.errors || []).map((error, index) => (
        <div key={`${error.code}-${index}`}>
          <code>{error.node_id || error.field_path || error.code}</code> {error.message}
        </div>
      ))}
    </div>
  )
}
