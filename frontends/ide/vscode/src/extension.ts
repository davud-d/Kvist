import * as vscode from 'vscode';
import { KvistLauncher } from './launcher';
import { BinaryResolver } from './binaryResolver';
import { SelectionActions } from './selectionActions';
import { CommitMessageGenerator } from './commitMessage';
import { KvistChatView } from './chatView';
import { KvistInlineCompletionProvider } from './completionProvider';

export function activate(context: vscode.ExtensionContext) {
    const launcher = new KvistLauncher(context);
    const commitMessageGenerator = new CommitMessageGenerator(context);
    const chat = new KvistChatView(context);
    const inlineCompletionProvider = new KvistInlineCompletionProvider(context);

    const selectionActions = new SelectionActions(new BinaryResolver(context));
    context.subscriptions.push(
        selectionActions,
        ...(['rewriteAsSenior', 'translateSelection', 'explainAndFix'] as const).map(action =>
            vscode.commands.registerCommand(`kvist.${action}`, (uri?: vscode.Uri) => selectionActions.run(action, uri))),
        chat,
        vscode.window.registerWebviewViewProvider(KvistChatView.viewType, chat, {
            webviewOptions: { retainContextWhenHidden: true }
        }),
        inlineCompletionProvider,
        vscode.languages.registerInlineCompletionItemProvider({ scheme: 'file' }, inlineCompletionProvider),
        vscode.commands.registerCommand('kvist.focus', () => vscode.commands.executeCommand(`${KvistChatView.viewType}.focus`)),
        vscode.commands.registerCommand('kvist.newSession', () => chat.invoke({ type: 'invoke', name: 'new', args: [] })),
        vscode.commands.registerCommand('kvist.settings', () => chat.invoke({ type: 'showSettings' })),
        vscode.commands.registerCommand('kvist.launch', () => {
            launcher.launch();
        }),
        vscode.commands.registerCommand('kvist.generateCommitMessage', async () => {
            await commitMessageGenerator.generate();
        })
    );
}

export function deactivate() {}
