// Browser example. Proxy /projects to the local API on the frontend dev server.
// Do not embed a real deployment credential in a public frontend bundle.
export async function chat({ projectId, message, conversationId, token, signal, onEvent }) {
  const response = await fetch(`/projects/${encodeURIComponent(projectId)}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  });
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', finished = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const lines = frame.split('\n');
        const type = lines.find(line => line.startsWith('event: '))?.slice(7);
        const data = lines.filter(line => line.startsWith('data: ')).map(line => line.slice(6)).join('\n');
        if (type && data) {
          const payload = JSON.parse(data);
          onEvent(type, payload);
          if (type === 'error') throw new Error(payload.message);
          if (type === 'done') finished = true;
        }
      }
      if (done) break;
    }
    if (!finished) throw new Error('Stream ended before completion');
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
