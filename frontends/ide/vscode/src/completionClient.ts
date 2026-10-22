import type * as vscode from 'vscode';
import { spawn, ChildProcess } from 'child_process';
import { createHash } from 'crypto';

/** The wire shape `kvist --complete` reads on stdin. */
export interface CompletionRequest {
    file_path: string;
    language: string;
    prefix: string;
    suffix: string;
}

/** What to put in the editor: `text` replaces `before` UTF-16 units left of the cursor
 * through `after` units right of it. Both are 0 for a plain continuation. */
export interface Completion {
    text: string;
    before: number;
    after: number;
}

export const NO_COMPLETION: Completion = { text: '', before: 0, after: 0 };

/** One line of the `kvist --complete-server` reply stream. */
interface ServerMessage {
    id: number;
    delta?: string;
    replace?: Completion;
    done?: boolean;
    error?: string;
}

// A file past this size costs more to serialize and send than any
// completion is worth; the core defensively caps its input as well.
export const MAX_COMPLETION_BUFFER_CHARS = 500_000;

const DEFAULT_CACHE_LIMIT = 50;

export function isDocumentTooLargeForCompletion(fullText: string): boolean {
    return fullText.length > MAX_COMPLETION_BUFFER_CHARS;
}

export function buildCacheKey(filePath: string, prefix: string, suffix: string): string {
    return createHash('sha256').update(JSON.stringify([filePath, prefix, suffix])).digest('hex');
}

/** Builds the wire request from plain values, so the prefix/suffix split and path
 * normalization are testable without a real vscode.TextDocument. */
export function buildCompletionRequest(
    filePath: string,
    language: string,
    fullText: string,
    cursorOffset: number
): CompletionRequest {
    return {
        file_path: filePath.replace(/\\/g, '/'),
        language: language.toLowerCase(),
        prefix: fullText.slice(0, cursorOffset),
        suffix: fullText.slice(cursorOffset)
    };
}

function isReplacement(value: unknown): value is Completion {
    const edit = value as Partial<Completion> | null;
    return typeof edit === 'object' && edit !== null && typeof edit.text === 'string'
        && Number.isInteger(edit.before) && Number.isInteger(edit.after);
}

export function parseServerMessage(line: string): ServerMessage | undefined {
    try {
        const parsed = JSON.parse(line);
        if (typeof parsed === 'object' && parsed !== null && typeof parsed.id === 'number') {
            // A malformed replacement must not reach the editor as a range.
            return isReplacement(parsed.replace) || parsed.replace === undefined
                ? parsed as ServerMessage : { id: parsed.id, error: 'malformed replacement' };
        }
    } catch {
        // A stray non-JSON line (a library warning) is not a reply.
    }
    return undefined;
}

/** A small recency cache keyed by cursor context: re-triggering at the same
 * position (e.g. after an accepted-then-undone suggestion) answers instantly
 * instead of calling the model again. */
export class CompletionCache<T = string> {
    private readonly entries = new Map<string, T>();

    constructor(private readonly limit: number = DEFAULT_CACHE_LIMIT) {}

    get(key: string): T | undefined {
        const value = this.entries.get(key);
        if (value === undefined) {
            return undefined;
        }
        // Refresh recency on read, so a cache hit keeps this key alive.
        this.entries.delete(key);
        this.entries.set(key, value);
        return value;
    }

    set(key: string, value: T): void {
        this.entries.delete(key);
        this.entries.set(key, value);
        if (this.entries.size > this.limit) {
            const oldest = this.entries.keys().next().value;
            if (oldest !== undefined) {
                this.entries.delete(oldest);
            }
        }
    }

    recentValues(): T[] {
        return [...this.entries.values()].reverse();
    }

    get size(): number {
        return this.entries.size;
    }
}

/** History is scoped to the binary/workspace/language and exact buffer context.
 * Matching forward typing consumes a continuation; deleting text restores the anchor.
 * A replacement is only reused at its exact anchor: typing changes what it replaces. */
export class CompletionHistory {
    private readonly cache = new CompletionCache<{
        scope: string; request: CompletionRequest; completion: Completion;
    }>();

    get(scope: string, request: CompletionRequest): Completion | undefined {
        const exact = this.cache.get(this.key(scope, request));
        if (exact) { return exact.completion; }
        for (const entry of this.cache.recentValues()) {
            const old = entry.request;
            if (entry.completion.before || entry.completion.after) { continue; }
            if (scope !== entry.scope || request.file_path !== old.file_path || request.language !== old.language
                || request.suffix !== old.suffix || !request.prefix.startsWith(old.prefix)) { continue; }
            const typed = request.prefix.slice(old.prefix.length);
            if (!entry.completion.text.startsWith(typed)) { continue; }
            const rest = entry.completion.text.slice(typed.length);
            if (rest) { return { text: rest, before: 0, after: 0 }; }
        }
        return undefined;
    }

    set(scope: string, request: CompletionRequest, completion: Completion): void {
        this.cache.set(this.key(scope, request), { scope, request: { ...request }, completion });
    }

    private key(scope: string, request: CompletionRequest): string {
        return JSON.stringify([scope, request.language, buildCacheKey(request.file_path, request.prefix, request.suffix)]);
    }
}

const MAX_COMPLETION_CHARS = 2000;
const REQUEST_TIMEOUT_MS = 6000;

interface PendingRequest {
    text: string;
    replacement?: Completion;
    onDone(completion: Completion): void;
    onFail(): void;
}

interface Server {
    key: string;
    child: ChildProcess;
    pending: Map<number, PendingRequest>;
    buffer: string;
}

/** Cancellable completion transport over one long-lived `kvist --complete-server`
 * process per binary/workspace. Starting a process per keystroke paid interpreter
 * start-up and a new TLS connection each time. Cache hits also cancel stale work. */
export class CompletionClient {
    private readonly history = new CompletionHistory();
    private cancelPending: (() => void) | undefined;
    private server: Server | undefined;
    private nextId = 1;

    constructor(private debounceMs: number, private readonly spawnProcess: typeof spawn = spawn) {}

    setDebounceMs(debounceMs: number): void {
        this.debounceMs = Number.isFinite(debounceMs) ? Math.max(0, debounceMs) : 100;
    }

    requestCompletion(
        binaryPath: string,
        workspaceDir: string,
        request: CompletionRequest,
        token: vscode.CancellationToken,
        explicit = false
    ): Promise<Completion> {
        this.cancelPending?.();
        if (token.isCancellationRequested) { return Promise.resolve(NO_COMPLETION); }
        const scope = JSON.stringify([binaryPath, workspaceDir]);
        const cached = this.history.get(scope, request);
        if (cached !== undefined) { return Promise.resolve(cached); }

        return new Promise<Completion>((resolve) => {
            let timer: ReturnType<typeof setTimeout> | undefined;
            let timeout: ReturnType<typeof setTimeout> | undefined;
            let subscription: vscode.Disposable | undefined;
            let sent: { server: Server; id: number } | undefined;
            let done = false;
            const finish = (value: Completion, stopRemote: boolean) => {
                if (done) { return; }
                done = true;
                clearTimeout(timer);
                clearTimeout(timeout);
                subscription?.dispose();
                if (sent) { this.release(sent.server, sent.id, stopRemote); }
                if (this.cancelPending === cancel) { this.cancelPending = undefined; }
                resolve(value);
            };
            const cancel = () => finish(NO_COMPLETION, true);
            this.cancelPending = cancel;
            subscription = token.onCancellationRequested(cancel);
            if (done || token.isCancellationRequested) {
                subscription.dispose();
                cancel();
                return;
            }
            timer = setTimeout(() => {
                if (done || token.isCancellationRequested) { cancel(); return; }
                const server = this.ensureServer(binaryPath, workspaceDir);
                if (!server) { finish(NO_COMPLETION, false); return; }
                const id = this.nextId++;
                sent = { server, id };
                server.pending.set(id, {
                    text: '',
                    onDone: completion => {
                        this.history.set(scope, request, completion);
                        finish(completion, false);
                    },
                    onFail: () => finish(NO_COMPLETION, false)
                });
                timeout = setTimeout(cancel, REQUEST_TIMEOUT_MS);
                // `edits`: this editor can draw a replacement, so the core may offer one.
                server.child.stdin?.write(JSON.stringify({ id, edits: true, ...request }) + '\n');
            }, explicit ? 0 : this.debounceMs);
        });
    }

    dispose(): void {
        this.cancelPending?.();
        if (this.server) { this.dropServer(this.server); }
    }

    private release(server: Server, id: number, stopRemote: boolean): void {
        if (!server.pending.delete(id) || !stopRemote || this.server !== server) { return; }
        server.child.stdin?.write(JSON.stringify({ cancel: id }) + '\n');
    }

    private ensureServer(binaryPath: string, workspaceDir: string): Server | undefined {
        const key = JSON.stringify([binaryPath, workspaceDir]);
        if (this.server?.key === key) { return this.server; }
        if (this.server) { this.dropServer(this.server); }
        try {
            const child = this.spawnProcess(binaryPath, ['--workspace', workspaceDir, '--complete-server'], {
                stdio: ['pipe', 'pipe', 'pipe']
            });
            const server: Server = { key, child, pending: new Map(), buffer: '' };
            child.stdout?.on('data', data => this.receive(server, data.toString()));
            // Drain warnings and handle EPIPE if the binary exits before reading stdin.
            child.stderr?.resume();
            child.stdin?.on('error', () => this.dropServer(server));
            child.on('error', () => this.dropServer(server));
            child.on('close', () => this.dropServer(server));
            this.server = server;
            return server;
        } catch {
            return undefined;
        }
    }

    private dropServer(server: Server): void {
        if (this.server === server) { this.server = undefined; }
        const pending = [...server.pending.values()];
        server.pending.clear();
        server.child.kill();
        for (const request of pending) { request.onFail(); }
    }

    private receive(server: Server, chunk: string): void {
        server.buffer += chunk;
        const lines = server.buffer.split('\n');
        server.buffer = lines.pop() ?? '';
        for (const line of lines) {
            const message = parseServerMessage(line);
            const request = message && server.pending.get(message.id);
            if (!message || !request) { continue; }
            if (message.error !== undefined) { request.onFail(); }
            else if (message.replace) { request.replacement = message.replace; }
            else if (message.done) {
                request.onDone(request.replacement ?? { text: request.text, before: 0, after: 0 });
            }
            else if (typeof message.delta === 'string') {
                request.text = (request.text + message.delta).slice(0, MAX_COMPLETION_CHARS);
            }
        }
    }
}
