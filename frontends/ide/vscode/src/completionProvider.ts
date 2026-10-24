import * as vscode from 'vscode';
import { BinaryResolver } from './binaryResolver';
import { canReadCompletionDocument } from './completionSafety';
import { CompletionClient, buildCompletionRequest, isDocumentTooLargeForCompletion } from './completionClient';

const CONFIG_SECTION = 'kvist.inlineCompletion';
const DEFAULT_DEBOUNCE_MS = 100;

/**
 * Inline completion (ghost text) provider. Requests go to a dedicated
 * `kvist --complete-server` process — never the chat core, so a slow or busy
 * agent run never starves completion.
 */
export class KvistInlineCompletionProvider implements vscode.InlineCompletionItemProvider, vscode.Disposable {
    private readonly binaryResolver: BinaryResolver;
    private readonly client: CompletionClient;

    constructor(context: vscode.ExtensionContext) {
        this.binaryResolver = new BinaryResolver(context);
        this.client = new CompletionClient(this.debounceMs());
    }

    dispose(): void {
        this.client.dispose();
    }

    async provideInlineCompletionItems(
        document: vscode.TextDocument,
        position: vscode.Position,
        context: vscode.InlineCompletionContext,
        token: vscode.CancellationToken
    ): Promise<vscode.InlineCompletionItem[] | undefined> {
        if (!this.isEnabled()) {
            return undefined;
        }

        const folder = vscode.workspace.getWorkspaceFolder(document.uri);
        if (!folder) {
            return undefined;
        }

        if (document.uri.scheme !== 'file') { return undefined; }
        const fullText = canReadCompletionDocument(document.uri.fsPath, () => document.getText());
        if (fullText === undefined || isDocumentTooLargeForCompletion(fullText)) {
            return undefined;
        }

        const binaryPath = this.binaryResolver.resolveBinaryPath();
        if (!binaryPath || this.binaryResolver.validateBinary(binaryPath)) {
            return undefined;
        }

        const filePath = vscode.workspace.asRelativePath(document.uri, false);
        const request = buildCompletionRequest(filePath, document.languageId, fullText, document.offsetAt(position));

        // The setting can change between requests (a settings.json edit), so re-read
        // it here rather than freezing the value at construction time.
        this.client.setDebounceMs(this.debounceMs());
        const version = document.version;
        const completion = await this.client.requestCompletion(
            binaryPath, folder.uri.fsPath, request, token,
            context.triggerKind === vscode.InlineCompletionTriggerKind.Invoke
        );
        if (token.isCancellationRequested || document.version !== version || !completion.text.trim()) {
            return undefined;
        }
        const offset = document.offsetAt(position);
        const range = new vscode.Range(
            document.positionAt(offset - completion.before), document.positionAt(offset + completion.after)
        );
        const item = new vscode.InlineCompletionItem(completion.text, range);
        if (completion.before || completion.after) {
            // A plain item is ghost text: VS Code shows it only when what was typed in its range is a
            // prefix of the new text, which a correction never is. `isInlineEdit` (proposed API
            // `inlineCompletionsAdditions`, ignored unless the user enables it for this extension) draws
            // it as a real edit with the old text struck through; `filterText` keeps the item from being
            // dropped before that.
            item.filterText = document.getText(range);
            Object.assign(item, { isInlineEdit: true });
        }
        return [item];
    }

    private isEnabled(): boolean {
        return vscode.workspace.getConfiguration(CONFIG_SECTION).get<boolean>('enabled', true);
    }

    private debounceMs(): number {
        return vscode.workspace.getConfiguration(CONFIG_SECTION).get<number>('debounceMs', DEFAULT_DEBOUNCE_MS);
    }
}
