import * as vscode from 'vscode';
import { BinaryResolver } from './binaryResolver';
import { CompletionClient, buildCompletionRequest, isDocumentTooLargeForCompletion } from './completionClient';

const CONFIG_SECTION = 'kvist.inlineCompletion';
const DEFAULT_DEBOUNCE_MS = 250;

/**
 * Inline completion (ghost text) provider. Each request is a fresh, stateless
 * `kvist --complete` process — never the long-lived chat core, so a slow or
 * busy agent run never starves completion (docs/autocomplete-plan.md section 1).
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
        _context: vscode.InlineCompletionContext,
        token: vscode.CancellationToken
    ): Promise<vscode.InlineCompletionItem[] | undefined> {
        if (!this.isEnabled()) {
            return undefined;
        }

        const folder = vscode.workspace.getWorkspaceFolder(document.uri);
        if (!folder) {
            return undefined;
        }

        const fullText = document.getText();
        if (isDocumentTooLargeForCompletion(fullText)) {
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
        const completion = await this.client.requestCompletion(binaryPath, folder.uri.fsPath, request, token);
        if (!completion) {
            return undefined;
        }
        return [new vscode.InlineCompletionItem(completion, new vscode.Range(position, position))];
    }

    private isEnabled(): boolean {
        return vscode.workspace.getConfiguration(CONFIG_SECTION).get<boolean>('enabled', true);
    }

    private debounceMs(): number {
        return vscode.workspace.getConfiguration(CONFIG_SECTION).get<number>('debounceMs', DEFAULT_DEBOUNCE_MS);
    }
}
