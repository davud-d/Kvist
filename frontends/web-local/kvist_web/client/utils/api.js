let csrf = document.querySelector('meta[name="kvist-csrf"]').content;
const API_ROOT = "/api/v1";
export class ApiError extends Error {
    constructor(message, status) {
        super(message);
        this.name = "ApiError";
        this.status = status;
    }
}
async function decode(response) {
    const value = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
    if (!response.ok || value.ok === false)
        throw new ApiError(value.error || `HTTP ${response.status}`, response.status);
    return value;
}
function get(resource, params = {}) {
    const url = new URL(`${API_ROOT}${resource}`, location.origin);
    for (const [key, value] of Object.entries(params))
        url.searchParams.set(key, value);
    return fetch(url, { credentials: "same-origin" }).then(decode);
}
function post(resource, body = {}) {
    return fetch(`${API_ROOT}${resource}`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Kvist-CSRF": csrf },
        body: JSON.stringify(body),
    }).then(decode);
}
function ref(selection) {
    return { server_id: selection.serverId, workspace: selection.workspace, session: selection.session };
}
// This is the browser's only backend boundary. Components consume domain
// operations and protocol DTOs; they never depend on controller, storage,
// process, or SSH implementation details.
export const api = Object.freeze({
    bootstrap: () => get("/bootstrap"),
    sshAliases: () => get("/ssh-aliases"),
    configuration: serverId => get("/configuration", { server: serverId }),
    saveConfiguration: (serverId, value) => post("/configuration", { ...value, server_id: serverId }),
    chatgptAuth: (serverId, value) => post("/configuration/chatgpt-auth", { ...value, server_id: serverId }),
    modelCatalog: (serverId, value) => post("/configuration/models", { ...value, server_id: serverId }),
    command: (selection, name, args) => post("/command", { ...ref(selection), name, args }),
    renewSession: async () => {
        const response = await fetch("/", { credentials: "same-origin", cache: "no-store" });
        if (!response.ok)
            throw new ApiError("Could not restore the browser connection.", response.status);
        const shell = new DOMParser().parseFromString(await response.text(), "text/html");
        const token = shell.querySelector('meta[name="kvist-csrf"]')?.content;
        if (!token)
            throw new Error("The server did not provide a browser session.");
        csrf = token;
    },
    navigation: () => get("/navigation"),
    history: (selection, after = 0, limit = 1000) => get("/history", { server: selection.serverId, workspace: selection.workspace, session: selection.session, after, limit }),
    changes: selection => get("/changes", { server: selection.serverId, workspace: selection.workspace, session: selection.session }),
    createSession: selection => post("/sessions", { server_id: selection.serverId, workspace: selection.workspace }),
    send: (selection, text) => post("/send", { ...ref(selection), text }),
    stop: selection => post("/stop", ref(selection)),
    // "pause": the agent and its helpers stop at a safe point and keep the task; "helper_stop": one helper.
    control: (selection, kind, extra = {}) => post("/control", { ...ref(selection), kind, ...extra }),
    respond: (selection, response) => post("/respond", { ...ref(selection), response }),
    markRead: (selection, seq) => post("/read", { ...ref(selection), seq }),
    addWorkspace: (serverId, path) => post("/workspaces", { server_id: serverId, path }),
    addServer: server => post("/servers", server),
    connectServer: (serverId, fingerprint = null, attemptId = "") => post("/servers/connect", { server_id: serverId, attempt_id: attemptId, ...(fingerprint ? { fingerprint } : {}) }),
    configSyncPreview: () => get("/config-sync"),
    syncConfig: (serverId, credentials) => post("/config-sync", { server_id: serverId, credentials }),
    events: (after, epoch = "") => {
        const query = new URLSearchParams({ after: String(after) });
        if (epoch)
            query.set("epoch", epoch);
        return new EventSource(`${API_ROOT}/events?${query}`);
    },
});
