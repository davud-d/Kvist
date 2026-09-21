import * as vscode from 'vscode';
import { KvistLauncher } from './launcher';
import { CommitMessageGenerator } from './commitMessage';

export function activate(context: vscode.ExtensionContext) {
    const launcher = new KvistLauncher(context);
    const commitMessageGenerator = new CommitMessageGenerator(context);

    context.subscriptions.push(
        vscode.commands.registerCommand('kvist.launch', () => {
            launcher.launch();
        })
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('kvist.generateCommitMessage', async () => {
            await commitMessageGenerator.generate();
        })
    );
}

export function deactivate() {}
