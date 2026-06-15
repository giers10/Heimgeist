import React from 'react'

export default function WorkflowConfirmationDialog({ confirmation, onRespond }) {
  if (!confirmation) return null
  const args = confirmation.payload?.arguments || {}
  return (
    <div className="workflow-confirmation-overlay" role="dialog" aria-modal="true">
      <div className="workflow-confirmation-dialog">
        <h3>Confirm workflow action</h3>
        <p>This workflow wants to run <code>{confirmation.payload?.tool}</code>.</p>
        {args.library_slug && <p><strong>Destination:</strong> {args.library_slug}</p>}
        {args.title && <p><strong>Title:</strong> {args.title}</p>}
        {args.message_id && <p><strong>Message:</strong> {args.message_id}</p>}
        {args.url && <p><strong>URL:</strong> {args.url}</p>}
        <div className="workflow-confirmation-actions">
          <button className="button ghost" type="button" onClick={() => onRespond(false)}>Reject</button>
          <button className="button" type="button" onClick={() => onRespond(true)}>Approve</button>
        </div>
      </div>
    </div>
  )
}
