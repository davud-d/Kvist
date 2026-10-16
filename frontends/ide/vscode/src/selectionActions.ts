import * as vscode from 'vscode';
import { spawn } from 'node:child_process';
import { BinaryResolver } from './binaryResolver';
import { buildSelectionRequest, parseSelectionReport, parseSelectionResponse, selectionIsCurrent, SelectionAction } from './selectionTask';

export class SelectionActions implements vscode.Disposable {
    private readonly previews = new Map<string, string>();
    private readonly provider: vscode.Disposable;
    private readonly pending = new Set<vscode.CancellationTokenSource>();
    private sequence = 0;
    private busy = false;

    constructor(private readonly resolver: BinaryResolver) {
        this.provider = vscode.workspace.registerTextDocumentContentProvider('kvist-selection', {
            provideTextDocumentContent: uri => this.previews.get(uri.toString()) ?? '',
        });
    }

    dispose() {
        for (const source of this.pending) { source.cancel(); source.dispose(); }
        this.provider.dispose();
        this.previews.clear();
    }

    async run(action: SelectionAction, uri?: vscode.Uri): Promise<void> {
        const editor = vscode.window.activeTextEditor;
        if (!editor || editor.selection.isEmpty || (uri && uri.toString() !== editor.document.uri.toString())) {
            await vscode.window.showWarningMessage('Select text in the editor first.');
            return;
        }
        if (this.busy) { await vscode.window.showWarningMessage('A Kvist selection task is already running.'); return; }
        const document = editor.document;
        const range = new vscode.Range(editor.selection.start, editor.selection.end);
        const version = document.version;
        const original = document.getText(range);
        const fullText = document.getText();
        const start = document.offsetAt(range.start);
        const end = document.offsetAt(range.end);
        const current = () => !document.isClosed && selectionIsCurrent(version, document.version, original, document.getText(range));
        this.busy = true;
        const previewUris: vscode.Uri[] = [];
        try {
            let language = '';
            if (action === 'translateSelection') {
                const choice = await vscode.window.showQuickPick(['English', 'Russian', 'Kazakh', 'Kyrgyz', 'Arabic', 'British English', 'American English', 'Custom language…'], { title: 'Translate Selection To' });
                if (!choice) return;
                language = choice === 'Custom language…' ? (await vscode.window.showInputBox({ title: 'Kvist: Custom Language', prompt: 'Language name', validateInput: value => value.trim() && value.length <= 60 && !/[\r\n]/.test(value) ? undefined : 'Enter a language name (1–60 characters).' }))?.trim() ?? '' : choice;
                if (!language) return;
            }
            const binary = this.resolver.resolveBinaryPath();
            if (!binary) throw new Error('Kvist binary was not found.');
            const error = this.resolver.validateBinary(binary);
            if (error) throw new Error(error);
            const request = buildSelectionRequest(action, {
                text: original, before: fullText.slice(Math.max(0, start - 4000), start),
                after: fullText.slice(end, end + 4000), language: document.languageId,
                file: vscode.workspace.asRelativePath(document.uri),
            }, language);
            const reply = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'Kvist: processing selection', cancellable: true }, async (_progress, token) => {
                const source = new vscode.CancellationTokenSource();
                const subscription = token.onCancellationRequested(() => source.cancel());
                this.pending.add(source);
                if (token.isCancellationRequested) source.cancel();
                try { return await this.request(binary, request, source.token); }
                finally { subscription.dispose(); this.pending.delete(source); source.dispose(); }
            });
            if (reply === null) return;
            let replacement: string | null = reply;
            if (action === 'explainAndFix') {
                const report = parseSelectionReport(reply);
                const reportUri = this.preview('review.txt', [report.explanation, 'Risks:', ...report.risks, 'Bugs:', ...report.bugs, 'Patch safety:', report.patch_safety ?? 'No patch proposed.'].join('\n\n'), previewUris);
                await vscode.window.showTextDocument(reportUri, { preview: true, viewColumn: vscode.ViewColumn.Beside });
                replacement = report.patch;
                if (!replacement || !report.patch_safety?.trim()) return;
                if (await vscode.window.showInformationMessage('Kvist review is ready.', 'Preview Patch') !== 'Preview Patch') return;
            }
            if (!replacement?.trim()) throw new Error('Kvist returned an empty replacement.');
            if (replacement === original) { await vscode.window.showInformationMessage('Kvist suggested no changes.'); return; }
            if (!current()) throw new Error('Document changed, retry action.');
            const before = this.preview('before/' + document.uri.path.split('/').pop(), fullText, previewUris);
            const after = this.preview('after/' + document.uri.path.split('/').pop(), fullText.slice(0, start) + replacement + fullText.slice(end), previewUris);
            await vscode.commands.executeCommand('vscode.diff', before, after, 'Kvist: Selection Changes');
            if (await vscode.window.showInformationMessage('Review the diff before applying the selection replacement.', 'Apply', 'Cancel') !== 'Apply') return;
            if (!current()) throw new Error('Document changed, retry action.');
            // No await between the final version check and editor.edit: the edit targets
            // the captured editor and VS Code rejects a concurrent document change.
            const applied = await editor.edit(edit => edit.replace(range, replacement!), { undoStopBefore: true, undoStopAfter: true });
            if (!applied) throw new Error('Could not apply the replacement. Retry action.');
        } catch (error) {
            await vscode.window.showErrorMessage(`Kvist: ${error instanceof Error ? error.message : String(error)}`);
        } finally {
            this.busy = false;
            // Open preview documents retain their text; the provider no longer holds source copies.
            for (const uri of previewUris) this.previews.delete(uri.toString());
        }
    }

    private preview(name: string, text: string, uris: vscode.Uri[]): vscode.Uri {
        const uri = vscode.Uri.from({ scheme: 'kvist-selection', path: `/${++this.sequence}/${name}` });
        this.previews.set(uri.toString(), text);
        uris.push(uri);
        return uri;
    }

    private request(binary: string, payload: unknown, token: vscode.CancellationToken): Promise<string | null> {
        if (token.isCancellationRequested) return Promise.resolve(null);
        return new Promise((resolve, reject) => {
            const child = spawn(binary, ['--selection-task'], { stdio: ['pipe', 'pipe', 'pipe'] });
            let stdout = '';
            let stderr = '';
            let settled = false;
            const finish = (error?: Error, value: string | null = null) => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                cancellation.dispose();
                if (error) reject(error); else resolve(value);
            };
            const timer = setTimeout(() => { child.kill(); finish(new Error('Kvist did not answer within 120 seconds.')); }, 120_000);
            const cancellation = token.onCancellationRequested(() => { child.kill(); finish(); });
            child.stdout.setEncoding('utf8');
            child.stderr.setEncoding('utf8');
            child.stdout.on('data', (data: string) => {
                stdout += data;
                if (stdout.length > 8 * 1024 * 1024) { child.kill(); finish(new Error('Kvist reply is too large.')); }
            });
            child.stderr.on('data', (data: string) => { stderr = (stderr + data).slice(-8000); });
            child.on('error', error => finish(error));
            child.stdin.on('error', error => finish(error));
            child.on('close', code => {
                if (settled) return;
                if (code !== 0) { finish(new Error(stderr.includes('--selection-task') ? 'The bundled Kvist binary is too old for editor actions. Rebuild it.' : `Kvist exited with code ${code}.`)); return; }
                try { finish(undefined, parseSelectionResponse(stdout)); }
                catch (error) { finish(error instanceof Error ? error : new Error(String(error))); }
            });
            child.stdin.end(JSON.stringify(payload));
        });
    }
}
