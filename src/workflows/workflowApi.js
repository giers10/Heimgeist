import { expectBackendJson, readBackendErrorText } from '../backendApi'

export async function fetchTools(apiBase) {
  return expectBackendJson(await fetch(`${apiBase}/tools`))
}

export async function fetchWorkflows(apiBase) {
  return expectBackendJson(await fetch(`${apiBase}/workflows`))
}

export async function fetchWorkflow(apiBase, workflowId) {
  return expectBackendJson(await fetch(`${apiBase}/workflows/${workflowId}`))
}

export async function createWorkflow(apiBase, payload) {
  return expectBackendJson(await fetch(`${apiBase}/workflows`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }))
}

export async function updateWorkflow(apiBase, workflowId, payload) {
  return expectBackendJson(await fetch(`${apiBase}/workflows/${workflowId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }))
}

export async function duplicateWorkflow(apiBase, workflowId) {
  return expectBackendJson(await fetch(`${apiBase}/workflows/${workflowId}/duplicate`, { method: 'POST' }))
}

export async function saveWorkflowRevision(apiBase, workflowId, graph) {
  return expectBackendJson(await fetch(`${apiBase}/workflows/${workflowId}/revisions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph }),
  }))
}

export async function validateWorkflow(apiBase, graph) {
  return expectBackendJson(await fetch(`${apiBase}/workflows/validate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph }),
  }))
}

export async function deleteWorkflow(apiBase, workflowId) {
  return expectBackendJson(await fetch(`${apiBase}/workflows/${workflowId}`, { method: 'DELETE' }))
}

export async function createWorkflowRun(apiBase, payload, signal) {
  return expectBackendJson(await fetch(`${apiBase}/workflow-runs`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal,
  }))
}

export async function getWorkflowRun(apiBase, runId) {
  return expectBackendJson(await fetch(`${apiBase}/workflow-runs/${runId}`))
}

export async function cancelWorkflowRun(apiBase, runId) {
  return expectBackendJson(await fetch(`${apiBase}/workflow-runs/${runId}/cancel`, { method: 'POST' }))
}

export async function respondToConfirmation(apiBase, runId, confirmationId, approved) {
  return expectBackendJson(await fetch(`${apiBase}/workflow-runs/${runId}/confirmations/${confirmationId}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ approved }),
  }))
}

export async function streamWorkflowEvents(apiBase, runId, { signal, onEvent }) {
  const response = await fetch(`${apiBase}/workflow-runs/${runId}/events?follow=true`, { signal })
  if (!response.ok) throw new Error(await readBackendErrorText(response))
  const reader = response.body?.getReader()
  if (!reader) throw new Error('Workflow event stream is unavailable.')
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.trim()) continue
      onEvent(JSON.parse(line))
    }
    if (done) break
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer))
}
