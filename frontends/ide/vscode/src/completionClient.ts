import type * as vscode from 'vscode';
import { spawn, ChildProcess } from 'child_process';

/** The exact wire shape `kvist --complete` reads on stdin (docs/autocomplete-plan.md section 2). */
export interface CompletionRequest {
    file_path: string;
    language: string;
    prefix: string;
    suffix: string;
}

interface CompletionResponse {
    ok: boolean;
    completion?: string;
    error?: string;
}

// A file past this size costs more to serialize and spawn a process for than any
// completion is worth; the core would truncate its own input anyway (docs/autocomplete-plan.md).
export const MAX_COMPLETION_BUFFER_CHARS = 500_000;

// How much of the surrounding text anchors the cache key: enough that moving the
// cursor a few characters away misses (the completion would differ), short enough
// that retyping the same line after an accepted-then-undone suggestion still hits.
const CACHE_KEY_PREFIX_CHARS = 80;
const CACHE_KEY_SUFFIX_CHARS = 50;
const DEFAULT_CACHE_LIMIT = 50;

export function isDocumentTooLargeForCompletion(fullText: string): boolean {
    return fullText.length > MAX_COMPLETION_BUFFER_CHARS;
}

export function buildCacheKey(filePath: string, prefix: string, suffix: string): string {
    const prefixTail = prefix.slice(-CACHE_KEY_PREFIX_CHARS);
    const suffixHead = suffix.slice(0, CACHE_KEY_SUFFIX_CHARS);
    // NUL cannot appear in editor text, so it safely separates the three parts.
    return `${filePath}\0${prefixTail}\0${suffixHead}`;
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
export class CompletionCache {
    private readonly entries = new Map<string, string>();

    constructor(private readonly limit: number = DEFAULT_CACHE_LIMIT) {}

    get(key: string): string | undefined {
        const value = this.entries.get(key);
        if (value === undefined) {
            return undefined;
        }
        // Refresh recency on read, so a cache hit keeps this key alive.
        this.entries.delete(key);
        this.entries.set(key, value);
        return value;
    }

    set(key: string, value: string): void {
        this.entries.delete(key);
        this.entries.set(key, value);
        if (this.entries.size > this.limit) {
            const oldest = this.entries.keys().next().value;
            if (oldest !== undefined) {
                this.entries.delete(oldest);
            }
        }
    }

    get size(): number {
        return this.entries.size;
    }
}

/**
 * Spawns a fresh `kvist --workspace <dir> --complete` process per completion
 * request (never routed through the long-lived chat core, per
 * docs/autocomplete-plan.md section 1).
 *
 * One instance is shared across every keystroke, so it is deliberately
 * single-flight: a new request always supersedes whatever this instance was
 * still waiting on — matching the editor firing again on every keystroke
 * rather than queuing behind a slow reply.
 */
export class CompletionClient {
    private readonly cache = new CompletionCache();
    private activeChild: ChildProcess | undefined;
    private debounceTimer: ReturnType<typeof setTimeout> | undefined;
    private resolvePending: ((completion: string) => void) | undefined;

    constructor(private debounceMs: number) {}

    setDebounceMs(debounceMs: number): void {
        this.debounceMs = Math.max(0, debounceMs);
    }

    requestCompletion(
        binaryPath: string,
        workspaceDir: string,
        request: CompletionRequest,
        token: vscode.CancellationToken
    ): Promise<string> {
        const cacheKey = buildCacheKey(request.file_path, request.prefix, request.suffix);
        const cached = this.cache.get(cacheKey);
        if (cached !== undefined) {
            return Promise.resolve(cached);
        }

        // Only one request is ever in flight on this instance.
        this.supersedePending();

        return new Promise<string>((resolve) => {
            if (token.isCancellationRequested) {
                resolve('');
                return;
            }
            this.resolvePending = resolve;
            token.onCancellationRequested(() => this.settle(resolve, ''));

            this.debounceTimer = setTimeout(() => {
                this.debounceTimer = undefined;
                if (token.isCancellationRequested) {
                    this.settle(resolve, '');
                    return;
                }
                this.spawnRequest(binaryPath, workspaceDir, request, cacheKey, resolve);
            }, this.debounceMs);
        });
    }

    /** Kills any in-flight process and resolves its promise. Call on extension deactivation. */
    dispose(): void {
        this.supersedePending();
    }

    private supersedePending(): void {
        if (this.debounceTimer) {
            clearTimeout(this.debounceTimer);
            this.debounceTimer = undefined;
        }
        this.activeChild?.kill();
        this.activeChild = undefined;
        if (this.resolvePending) {
            const resolve = this.resolvePending;
            this.resolvePending = undefined;
            resolve('');
        }
    }

    /** Resolves `resolve` with `value`, but only if it is still the current request —
     * a slower, already-superseded request's late callback must not resolve twice. */
    private settle(resolve: (completion: string) => void, value: string): void {
        if (this.resolvePending !== resolve) {
            return;
        }
        this.resolvePending = undefined;
        if (this.debounceTimer) {
            clearTimeout(this.debounceTimer);
            this.debounceTimer = undefined;
        }
        this.activeChild?.kill();
        this.activeChild = undefined;
        resolve(value);
    }

    private spawnRequest(
        binaryPath: string,
        workspaceDir: string,
        request: CompletionRequest,
        cacheKey: string,
        resolve: (completion: string) => void
    ): void {
        const child = spawn(binaryPath, ['--workspace', workspaceDir, '--complete'], {
            stdio: ['pipe', 'pipe', 'pipe']
        });
        this.activeChild = child;

        let stdout = '';
        child.stdout?.on('data', (data) => {
            stdout += data.toString();
        });
        child.on('error', () => this.settle(resolve, ''));
        child.on('close', (code) => {
            if (this.resolvePending !== resolve) {
                return;
            }
            if (code !== 0) {
                this.settle(resolve, '');
                return;
            }
            const response = parseCompletionResponse(stdout);
            const completion = response.ok && typeof response.completion === 'string' ? response.completion : '';
            if (response.ok) {
                this.cache.set(cacheKey, completion);
            }
            this.settle(resolve, completion);
        });

        child.stdin?.write(JSON.stringify(request));
        child.stdin?.end();
    }
}
