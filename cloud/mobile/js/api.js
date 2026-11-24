// The one place that talks HTTP to Kvist Cloud. Every state-changing call carries X-Kvist-Client: the server
// refuses a POST without it (a cross-site form cannot set it).

import { localized } from "./format.js";

export class ApiError extends Error {
  constructor(status, code, message, extra = {}) {
    super(code);
    this.status = status;
    this.code = code;
    this.localized = localized(message);
    this.extra = extra;
  }
}

/** api("/api/tasks", {method: "POST", body: {...}}) -> parsed JSON; rejects with ApiError. */
export async function api(path, { method = "GET", body, signal } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      signal,
      headers: { "X-Kvist-Client": "1", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(0, "offline", "No connection. / Нет соединения.");
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // A proxy's error page: the status below says enough.
  }
  if (!response.ok) {
    const failure = data?.error ?? data?.detail ?? {};
    throw new ApiError(response.status, failure.code ?? `http_${response.status}`, failure.message ?? "", failure);
  }
  return data;
}

export const post = (path, body = {}) => api(path, { method: "POST", body });
