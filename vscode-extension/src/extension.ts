import * as vscode from 'vscode';
import { KvistLauncher } from './launcher';
import { CommitMessageGenerator } from './commitMessage';
import { KvistChatView } from './chatView';

export function activate(context: vscode.ExtensionContext) {
    const launcher = new KvistLauncher(context);
    const commitMessageGenerator = new CommitMessageGenerator(context);
    const chat = new KvistChatView(context);

    context.subscriptions.push(
        chat,
        vscode.window.registerWebviewViewProvider(KvistChatView.viewType, chat, {
            webviewOptions: { retainContextWhenHidden: true }
        }),
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
