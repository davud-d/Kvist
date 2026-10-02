import * as vscode from 'vscode';
import { BinaryResolver } from './binaryResolver';
import { SupportedOs, defaultPosixShell, isPosix } from './platform';

export class KvistLauncher {
    private binaryResolver: BinaryResolver;

    constructor(context: vscode.ExtensionContext) {
        this.binaryResolver = new BinaryResolver(context);
    }

    async launch() {
        const workspaceFolder = this.getWorkspaceFolder();
        if (!workspaceFolder) {
            vscode.window.showErrorMessage('No workspace folder is open.');
            return;
        }

        const binaryPath = this.binaryResolver.resolveBinaryPath();
        if (!binaryPath) {
            const os = this.binaryResolver.getCurrentOs();
            if (!os) {
                vscode.window.showErrorMessage('This operating system is not supported.');
            } else {
                vscode.window.showErrorMessage('Kvist binary was not found.');
            }
            return;
        }

        const validationError = this.binaryResolver.validateBinary(binaryPath);
        if (validationError) {
            vscode.window.showErrorMessage(`Failed to start Kvist: ${validationError}`);
            return;
        }

        const terminal = vscode.window.createTerminal({
            name: 'Kvist',
            cwd: workspaceFolder.uri.fsPath,
            shellPath: this.getShellPath(),
            shellArgs: this.getShellArgs(binaryPath, workspaceFolder.uri.fsPath)
        });

        terminal.show();
    }

    private getWorkspaceFolder(): vscode.WorkspaceFolder | undefined {
        const folders = vscode.workspace.workspaceFolders;
        if (!folders || folders.length === 0) {
            return undefined;
        }
        return folders[0];
    }

    private getShellPath(): string | undefined {
        const os = this.binaryResolver.getCurrentOs();
        
        if (os !== null && isPosix(os)) {
            // Use user's shell or fall back to the platform's default
            return process.env.SHELL || defaultPosixShell(os);
        }
        
        // On Windows, let VS Code use default shell
        return undefined;
    }

    private getShellArgs(binaryPath: string, workspacePath: string): string[] | undefined {
        const os = this.binaryResolver.getCurrentOs();
        
        if (os !== null && isPosix(os)) {
            const shell = process.env.SHELL || defaultPosixShell(os);
            const shellName = shell.split('/').pop() || '';
            
            const command = `${this.quoteForShell(binaryPath)} --workspace ${this.quoteForShell(workspacePath)}`;
            
            // Use non-interactive shell to avoid startup files
            switch (shellName) {
                case 'fish':
                    return ['--no-config', '-c', command];
                case 'bash':
                    return ['--noprofile', '--norc', '-c', command];
                case 'zsh':
                    return ['-f', '-c', command];
                default:
                    return ['-c', command];
            }
        }
        
        // On Windows, use cmd to run the command
        if (os === SupportedOs.WINDOWS) {
            return ['/c', `"${binaryPath}" --workspace "${workspacePath}"`];
        }
        
        return undefined;
    }

    private quoteForShell(argument: string): string {
        // Escape special shell characters
        const escaped = argument
            .replace(/\\/g, '\\\\')
            .replace(/"/g, '\\"')
            .replace(/\$/g, '\\$')
            .replace(/`/g, '\\`');
        return `"${escaped}"`;
    }
}
