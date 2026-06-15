import React from 'react'
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import WorkflowNode from './WorkflowNode'

const nodeTypes = { workflow: WorkflowNode }

export default function WorkflowCanvas({ edges, nodes, onEdgesChange, onNodesChange, onSelectNode, onDropNode }) {
  const [instance, setInstance] = React.useState(null)
  return (
    <div className="workflow-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onInit={setInstance}
        onNodesChange={(changes) => onNodesChange(applyNodeChanges(changes, nodes))}
        onEdgesChange={(changes) => onEdgesChange(applyEdgeChanges(changes, edges))}
        onConnect={(connection) => onEdgesChange(addEdge({ ...connection, id: `${connection.source}-${connection.target}-${Date.now()}` }, edges))}
        onNodeClick={(_event, node) => onSelectNode(node.id)}
        onPaneClick={() => onSelectNode(null)}
        onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'move' }}
        onDrop={(event) => {
          event.preventDefault()
          const type = event.dataTransfer.getData('application/heimgeist-workflow-node')
          if (!type || !instance) return
          onDropNode(type, instance.screenToFlowPosition({ x: event.clientX, y: event.clientY }))
        }}
        fitView
      >
        <Background gap={20} />
        <MiniMap pannable zoomable />
        <Controls />
      </ReactFlow>
    </div>
  )
}
