// Every call to the server goes through here. Errors become {code, message, supportCode}: the
// screens show the translated code and, for a server failure, the code to quote — never a stack.

export class ApiError extends Error {
  constructor(status, code, supportCode = "") {
    super(code);
    this.status = status;
    this.code = code;
    this.supportCode = supportCode;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: {
        "X-Kvist-Client": "1",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network");
  }
  if (response.status === 204) return null;
  let data = null;
  try {
    data = await response.json();
  } catch {
    if (!response.ok) throw new ApiError(response.status, response.status >= 500 ? "internal" : "fallback");
    return null;
  }
  if (!response.ok) {
    const error = (data && data.error) || {};
    throw new ApiError(response.status, error.code || "fallback", error.support_code || "");
  }
  return data;
}

/** The task's live stream; reconnects on its own and resumes after the last stored event. */
export function openStream(taskId, { onMessage, onState }) {
  let lastSeq = 0;
  let socket = null;
  let stopped = false;
  let delay = 1000;
  const connect = () => {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/api/tasks/${taskId}/stream?after=${lastSeq}`);
    socket.onopen = () => { delay = 1000; onState("open"); };
    socket.onmessage = (message) => {
      const data = JSON.parse(message.data);
      if (data.type === "event" && data.seq) {
        if (data.seq <= lastSeq) return;
        lastSeq = data.seq;
      }
      onMessage(data);
    };
    socket.onclose = (event) => {
      if (stopped) return;
      if (event.code === 4401) { onState("signed_out"); return; }
      onState("reconnecting");
      setTimeout(connect, delay);
      delay = Math.min(delay * 2, 15000);
    };
  };
  connect();
  return () => { stopped = true; socket && socket.close(); };
}
