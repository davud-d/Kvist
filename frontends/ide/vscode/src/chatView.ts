import * as vscode from 'vscode';
import * as path from 'path';
import { execFile } from 'child_process';
import { KvistCore, type CoreCommand } from '../../../tui/ink/core';
import { sessionOp } from '../../../tui/ink/session';
import { BinaryResolver } from './binaryResolver';
import { splitEnvironment } from './environment';
import { pickWorkspaceFiles } from './filePicker';
import type { JournalEvent } from '../../../tui/ink/protocol';
import type { ConfigInfo, ProviderInfo, SessionInfo, ToHost, ToWebview } from './messages';

// The newest events of a session: enough for well over a hundred turns of a typical run.
const HISTORY_LIMIT = 1000;

// The provider ids offered on first run, in priority order: AnyModel first (Kvist's own
// shipped default, DEFAULT_BLANK_START_PROVIDER in llm_clients.py), then config_info.py's
// curated "built-in six". The other ~35 manual-tier vendors and custom endpoints stay
// reachable later through Settings once the panel is actually running.
const FIRST_RUN_PROVIDERS = ['anymodel', 'openai', 'anthropic', 'deepseek', 'openrouter', 'omniroute', 'freeflow', 'chatgpt'];

export class KvistChatView implements vscode.WebviewViewProvider {
    static readonly viewType = 'kvist.chat';

    private view: vscode.WebviewView | undefined;
    private core: KvistCore | undefined;
    private ready = false;
    private queued: ToWebview[] = [];
    private readonly binaryResolver: BinaryResolver;

    constructor(private readonly context: vscode.ExtensionContext) {
        this.binaryResolver = new BinaryResolver(context);
    }

    resolveWebviewView(view: vscode.WebviewView): void {
        this.view = view;
        this.ready = false;
        const media = vscode.Uri.joinPath(this.context.extensionUri, 'out');
        const resources = vscode.Uri.joinPath(this.context.extensionUri, 'resources');
        view.webview.options = { enableScripts: true, localResourceRoots: [media, resources] };
        view.webview.html = this.html(view.webview, media, resources);
        view.webview.onDidReceiveMessage((message: ToHost) => void this.onMessage(message));
        view.onDidDispose(() => {
            this.view = undefined;
            void this.core?.stop();
            this.core = undefined;
        });
    }

    // Focusing may be what creates the view, whose script has not started listening yet.
    async invoke(message: Extract<ToWebview, { type: 'invoke' | 'showSettings' }>): Promise<void> {
        await vscode.commands.executeCommand(`${KvistChatView.viewType}.focus`);
        if (this.ready) {
            this.post(message);
        } else {
            this.queued.push(message);
        }
    }

    dispose(): void {
        void this.core?.stop();
    }

    private post(message: ToWebview): void {
        void this.view?.webview.postMessage(message);
    }

    private async onMessage(message: ToHost): Promise<void> {
        switch (message.type) {
            case 'ready':
                this.ready = true;
                this.queued.splice(0).forEach((queued) => this.post(queued));
                return this.checkAndStart();
            case 'setProvider':
                return this.setProvider(message.provider, message.apiKey, message.model);
            case 'listModels':
                return this.listModels(message.provider, message.apiKey);
            case 'openRegistration':
                return this.openRegistration(message.provider);
            case 'submit':
                return this.send(() => this.core?.send(message.text));
            case 'command':
                return this.send(() => this.core?.sendCommand(message.name, message.args));
            case 'respond':
                return this.core?.respond(message.requestId, message.answer, message.remember);
            case 'cancel':
                return this.send(() => this.core?.cancel());
            case 'pause':
                return this.send(() => this.core?.pause());
            case 'stopHelper':
                return this.send(() => this.core?.stopHelper(message.agentId));
            case 'pickFiles':
                return this.post({ type: 'attached', paths: await pickWorkspaceFiles() });
            case 'getEnvironment':
                return this.post({ type: 'environment', env: this.environment() });
            case 'setEnvironment':
                return this.applyEnvironment(message.env);
            case 'getConfig':
                return this.sendConfig();
            case 'getSessions':
                return this.sendSessions();
            case 'getHistory':
                return this.sendHistory(message.name);
            case 'openFile':
                return this.openFile(message.path, message.line);
            case 'openLink':
                await vscode.env.openExternal(vscode.Uri.parse(message.url));
                return;
        }
    }

    private send(action: () => void): void {
        if (!this.core?.running) {
            this.post({ type: 'fault', message: 'The Kvist core is not running. Reload the window to restart it.' });
            return;
        }
        try {
            action();
        } catch (error) {
            this.post({ type: 'fault', message: error instanceof Error ? error.message : String(error) });
        }
    }

    private coreCommand(): CoreCommand | undefined {
        const binaryPath = this.binaryResolver.resolveBinaryPath();
        if (!binaryPath) {
            this.post({ type: 'fault', message: 'Kvist binary was not found. Set "kvist.binaryPath" or build the extension with the bundled core.' });
            return undefined;
        }
        const error = this.binaryResolver.validateBinary(binaryPath);
        if (error) {
            this.post({ type: 'fault', message: `Failed to start Kvist: ${error}` });
            return undefined;
        }
        return { command: binaryPath, prefixArgs: [] };
    }

    private workspacePath(): string | undefined {
        const folder = vscode.workspace.workspaceFolders?.[0];
        if (!folder) {
            this.post({ type: 'fault', message: 'Open a folder to work with Kvist.' });
        }
        return folder?.uri.fsPath;
    }

    // The core refuses to start at all without a usable provider (UI_API.md §11: "a
    // headless frontend needs [credentials] pre-configured"), so a genuinely first-ever
    // run must be caught here, before spawning it, rather than shown as a startup crash.
    private async checkAndStart(): Promise<void> {
        const command = this.coreCommand();
        const workspace = this.workspacePath();
        if (!command || !workspace) {
            return;
        }
        const info = await readJson<ConfigInfo>(command, workspace, ['--config-info']);
        const current = info?.providers.find((item) => item.id === info.saved_provider);
        if (info && !current?.has_credentials) {
            // FIRST_RUN_PROVIDERS' own order is the priority order (AnyModel first, Kvist's
            // own shipped default); the source list isn't sorted that way, so this is too.
            const providers = FIRST_RUN_PROVIDERS
                .map((id) => info.providers.find((item) => item.id === id))
                .filter((item): item is ProviderInfo => item !== undefined);
            this.post({ type: 'needsSetup', providers });
            return;
        }
        if (info) {
            this.post({ type: 'config', info });
        }
        return this.startCore();
    }

    private async setProvider(provider: string, apiKey: string, model: string): Promise<void> {
        const result = await this.runConfigOp<{ ok: boolean; error?: string }>('set-provider', { provider, api_key: apiKey, model });
        this.post({ type: 'setupResult', ok: result.ok, error: result.error });
        if (result.ok) {
            await this.checkAndStart();
        }
    }

    // The first-run screen's "which model?" field for a router/gateway provider (AnyModel,
    // OpenRouter, OmniRoute): offers the provider's live /v1/models catalog for the key the
    // person just typed, same as the terminal's own picker, before that key is ever saved.
    private async listModels(provider: string, apiKey: string): Promise<void> {
        const result = await this.runConfigOp<{ ok: boolean; error?: string; models?: string[] }>('list-models', { provider, api_key: apiKey });
        this.post({ type: 'modelList', provider, ok: result.ok, models: result.models ?? [], error: result.error });
    }

    // AnyModel is Kvist's own shipped default; the first-run screen offers to pop its signup
    // page the same way the terminal wizard does (`anymodel_onboarding.py`).
    private async openRegistration(provider: string): Promise<void> {
        const result = await this.runConfigOp<{ ok: boolean; opened?: boolean; url?: string | null }>('open-registration', { provider });
        this.post({ type: 'registrationOpened', provider, opened: result.opened === true, url: result.url ?? null });
    }

    private async runConfigOp<T extends { ok: boolean; error?: string }>(op: string, payload: unknown): Promise<T> {
        const command = this.coreCommand();
        const workspace = this.workspacePath();
        if (!command || !workspace) {
            return { ok: false, error: 'Kvist is not ready.' } as T;
        }
        return new Promise<T>((resolve) => {
            const child = execFile(
                command.command,
                [...command.prefixArgs, '--config-op', op],
                { cwd: workspace, timeout: op === 'set-provider' && (payload as { provider?: string })?.provider === 'chatgpt' ? 190_000 : 30_000, maxBuffer: 8 * 1024 * 1024 },
                (error, stdout) => {
                    try {
                        resolve(JSON.parse(stdout.trim().split('\n').at(-1) ?? '') as T);
                    } catch {
                        resolve({ ok: false, error: error?.message ?? 'Kvist returned an unreadable reply.' } as T);
                    }
                }
            );
            child.stdin?.end(JSON.stringify(payload));
        });
    }

    private async startCore(): Promise<void> {
        if (this.core?.running) {
            return;
        }
        const command = this.coreCommand();
        const workspace = this.workspacePath();
        if (!command || !workspace) {
            return;
        }

        // A window opens its own session, so a new run is never planned against an older launch's history.
        await sessionOp(command, workspace, 'launch');

        const core = new KvistCore(command);
        core.on('event', (event) => this.post({ type: 'event', event }));
        core.on('diagnostic', (text: string) => this.post({ type: 'diagnostic', text }));
        core.on('fault', (error: Error) => this.post({ type: 'fault', message: error.message }));
        core.on('exit', ({ code, expected }: { code: number | null; expected: boolean }) => this.post({ type: 'exit', expected, code }));
        core.start({ workspace, role: 'auto', approvalMode: 'ask', env: this.environment() });
        this.core = core;
    }

    private environment(): Record<string, string> {
        const saved = vscode.workspace.getConfiguration('kvist').get<unknown>('environment', {});
        const { valid, rejected } = splitEnvironment(saved);
        if (rejected.length > 0) {
            this.post({ type: 'fault', message: `Ignored invalid entries in "kvist.environment": ${rejected.join(', ')}` });
        }
        return valid;
    }

    // The core reads its environment once at launch, so a change means a new core. The
    // conversation stays on disk in its session; only the running process is replaced.
    private async applyEnvironment(requested: Record<string, string>): Promise<void> {
        const { valid, rejected } = splitEnvironment(requested);
        if (rejected.length > 0) {
            this.post({ type: 'fault', message: `Not saved, invalid variable names: ${rejected.join(', ')}` });
            return;
        }
        await vscode.workspace.getConfiguration('kvist').update('environment', valid, vscode.ConfigurationTarget.Global);
        this.post({ type: 'environment', env: valid });
        const previous = this.core;
        this.core = undefined;
        await previous?.stop();
        await this.startCore();
    }

    private async sendConfig(): Promise<void> {
        const command = this.coreCommand();
        const workspace = this.workspacePath();
        if (!command || !workspace) {
            return;
        }
        const info = await readJson<ConfigInfo>(command, workspace, ['--config-info']);
        if (info) {
            this.post({ type: 'config', info });
        }
    }

    private async sendSessions(): Promise<void> {
        const command = this.coreCommand();
        const workspace = this.workspacePath();
        if (!command || !workspace) {
            return;
        }
        // "list" is "status" without the whole histories of every session.
        const list = await readJson<SessionInfo>(command, workspace, ['--workspace', workspace, '--session-op', 'list']);
        if (!list) {
            return;
        }
        const items = list.session_items?.map(({ name, title, updated_at }) => ({ name, title, updated_at }));
        this.post({ type: 'sessions', info: { active_session: list.active_session, session_items: items } });
    }

    // What the user saw in a session, as the events the core recorded; the panel replays them.
    private async sendHistory(name: string): Promise<void> {
        const command = this.coreCommand();
        const workspace = this.workspacePath();
        if (!command || !workspace) {
            return;
        }
        const reply = await readJson<{ ok: boolean; error?: string; has_more?: boolean; events?: { event: JournalEvent }[] }>(
            command,
            workspace,
            ['--workspace', workspace, '--session-op', 'history', '--session-name', name, '--history-limit', String(HISTORY_LIMIT)]
        );
        if (!reply?.ok) {
            this.post({ type: 'fault', message: `Could not load the history of "${name}"${reply?.error ? `: ${reply.error}` : ''}.` });
            return;
        }
        this.post({ type: 'history', name, events: (reply.events ?? []).map((entry) => entry.event), hasMore: reply.has_more === true });
    }

    private async openFile(relativePath: string, line: number | null): Promise<void> {
        const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!root) {
            return;
        }
        const target = path.resolve(root, relativePath);
        // The path comes from the agent's output, so it must not lead outside the workspace.
        if (path.relative(root, target).startsWith('..')) {
            return;
        }
        const position = new vscode.Position(Math.max(0, (line ?? 1) - 1), 0);
        await vscode.window.showTextDocument(vscode.Uri.file(target), { selection: new vscode.Range(position, position) });
    }

    private html(webview: vscode.Webview, media: vscode.Uri, resources: vscode.Uri): string {
        const script = webview.asWebviewUri(vscode.Uri.joinPath(media, 'webview.js'));
        const style = webview.asWebviewUri(vscode.Uri.joinPath(media, 'webview.css'));
        const logo = webview.asWebviewUri(vscode.Uri.joinPath(resources, 'kvist-logo.png'));
        const nonce = randomNonce();
        const csp = [
            "default-src 'none'",
            `style-src ${webview.cspSource}`,
            `script-src 'nonce-${nonce}'`,
            `font-src ${webview.cspSource}`,
            // The agent's screen preview comes from Kvist's own loopback viewer.
            `img-src ${webview.cspSource} data: http://127.0.0.1:*`,
        ].join('; ');
        return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="${style}">
<title>Kvist</title>
</head>
<body>
<div id="root" data-logo="${logo}"></div>
<script nonce="${nonce}" src="${script}"></script>
</body>
</html>`;
    }
}

function readJson<T>(command: CoreCommand, workspace: string, args: string[]): Promise<T | undefined> {
    return new Promise((resolve) => {
        execFile(command.command, [...command.prefixArgs, ...args], { cwd: workspace, timeout: 20_000, maxBuffer: 64 * 1024 * 1024 }, (error, stdout) => {
            try {
                resolve(JSON.parse(stdout.trim().split('\n').at(-1) ?? '') as T);
            } catch {
                console.error('Unreadable Kvist reply', error?.message);
                resolve(undefined);
            }
        });
    });
}

function randomNonce(): string {
    const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    return Array.from({ length: 32 }, () => alphabet[Math.floor(Math.random() * alphabet.length)]).join('');
}
