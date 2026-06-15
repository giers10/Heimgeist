import React from 'react'
import { Handle, Position } from '@xyflow/react'

export default function WorkflowNode({ data, selected }) {
  return (
    <div className={`workflow-node workflow-node-${data.nodeType}${selected ? ' selected' : ''}`}>
      {data.nodeType !== 'input' && <Handle type="target" position={Position.Left} id="input" />}
      <div className="workflow-node-type">{data.nodeType}</div>
      <strong>{data.label}</strong>
      {data.tool && <small>{data.tool}</small>}
      {data.nodeType === 'condition' ? (
        <>
          <Handle type="source" position={Position.Right} id="true" style={{ top: '38%' }} />
          <Handle type="source" position={Position.Right} id="false" style={{ top: '72%' }} />
        </>
      ) : data.nodeType !== 'output' && <Handle type="source" position={Position.Right} id="output" />}
    </div>
  )
}
