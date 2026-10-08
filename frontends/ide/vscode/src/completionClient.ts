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

interface CompletionResponse {
    ok: boolean;
    completion?: string;
    completions?: string[];
    error?: string;
}

// A file past this size costs more to serialize and spawn a process for than any
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

export function parseCompletionResponse(stdout: string): CompletionResponse {
    const lastLine = stdout.trim().split('\n').pop() ?? '';
    try {
        const parsed = JSON.parse(lastLine);
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
            return { ok: false, error: 'Kvist returned an unreadable reply.' };
        }
        return parsed as CompletionResponse;
    } catch {
        return { ok: false, error: 'Kvist returned an unreadable reply.' };
    }
}

/** A small recency cache keyed by cursor context: re-triggering at the same
 * position (e.g. after an accepted-then-undone suggestion) answers instantly
 * instead of spawning a process again. */
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
 * Matching forward typing consumes a candidate; deleting text restores the anchor. */
export class CompletionHistory {
    private readonly cache = new CompletionCache<{
        scope: string; request: CompletionRequest; candidates: string[];
    }>();

    get(scope: string, request: CompletionRequest): string[] | undefined {
        const exact = this.cache.get(this.key(scope, request));
        if (exact) { return exact.candidates; }
        for (const entry of this.cache.recentValues()) {
            const old = entry.request;
            if (scope !== entry.scope || request.file_path !== old.file_path || request.language !== old.language
                || request.suffix !== old.suffix || !request.prefix.startsWith(old.prefix)) { continue; }
            const typed = request.prefix.slice(old.prefix.length);
            const matching = entry.candidates.filter(value => value.startsWith(typed));
            if (matching.length) { return matching.map(value => value.slice(typed.length)).filter(Boolean); }
        }
        return undefined;
    }

    set(scope: string, request: CompletionRequest, candidates: string[]): void {
        this.cache.set(this.key(scope, request), { scope, request: { ...request }, candidates: [...candidates] });
    }

    private key(scope: string, request: CompletionRequest): string {
        return JSON.stringify([scope, request.language, buildCacheKey(request.file_path, request.prefix, request.suffix)]);
    }
}

export function responseCandidates(response: CompletionResponse): string[] {
    if (!response.ok) { return []; }
    const values = Array.isArray(response.completions) ? response.completions : [response.completion];
    return [...new Set(values.filter((value): value is string => typeof value === 'string' && !!value.trim())
        .map(value => value.slice(0, 2000)))].slice(0, 3);
}

/** Single-flight, cancellable completion transport. Cache hits also cancel stale work. */
export class CompletionClient {
    private readonly history = new CompletionHistory();
    private cancelPending: (() => void) | undefined;

    constructor(private debounceMs: number, private readonly spawnProcess: typeof spawn = spawn) {}

    setDebounceMs(debounceMs: number): void {
        this.debounceMs = Number.isFinite(debounceMs) ? Math.max(0, debounceMs) : 2000;
    }

    requestCompletion(
        binaryPath: string,
        workspaceDir: string,
        request: CompletionRequest,
        token: vscode.CancellationToken,
        explicit = false
    ): Promise<string[]> {
        this.cancelPending?.();
        if (token.isCancellationRequested) { return Promise.resolve([]); }
        const scope = JSON.stringify([binaryPath, workspaceDir]);
        const cached = this.history.get(scope, request);
        if (cached !== undefined) { return Promise.resolve(cached); }

        return new Promise<string[]>((resolve) => {
            let child: ChildProcess | undefined;
            let timer: ReturnType<typeof setTimeout> | undefined;
            let timeout: ReturnType<typeof setTimeout> | undefined;
            let subscription: vscode.Disposable | undefined;
            let done = false;
            const finish = (values: string[]) => {
                if (done) { return; }
                done = true;
                clearTimeout(timer);
                clearTimeout(timeout);
                subscription?.dispose();
                child?.kill();
                if (this.cancelPending === cancel) { this.cancelPending = undefined; }
                resolve(values);
            };
            const cancel = () => finish([]);
            this.cancelPending = cancel;
            subscription = token.onCancellationRequested(cancel);
            if (done || token.isCancellationRequested) {
                subscription.dispose();
                cancel();
                return;
            }
            timer = setTimeout(() => {
                if (done || token.isCancellationRequested) { cancel(); return; }
                try {
                    child = this.spawnProcess(binaryPath, ['--workspace', workspaceDir, '--complete'], {
                        stdio: ['pipe', 'pipe', 'pipe']
                    });
                    timeout = setTimeout(cancel, 4000);
                    let stdout = '';
                    child.stdout?.on('data', data => {
                        stdout += data.toString();
                        if (stdout.length > 100_000) { cancel(); }
                    });
                    // Drain warnings and handle EPIPE if the binary exits before reading stdin.
                    child.stderr?.resume();
                    child.stdin?.on('error', cancel);
                    child.on('error', cancel);
                    child.on('close', code => {
                        if (done) { return; }
                        if (code !== 0) { cancel(); return; }
                        const response = parseCompletionResponse(stdout);
                        const values = responseCandidates(response);
                        if (response.ok) { this.history.set(scope, request, values); }
                        finish(values);
                    });
                    child.stdin?.end(JSON.stringify(request));
                } catch {
                    cancel();
                }
            }, explicit ? 0 : this.debounceMs);
        });
    }

    dispose(): void {
        this.cancelPending?.();
    }
}
