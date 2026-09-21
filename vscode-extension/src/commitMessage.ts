import * as vscode from 'vscode';
import { BinaryResolver } from './binaryResolver';
import { execFile, spawn, ChildProcess } from 'child_process';
import * as path from 'path';

interface CommitMessageResponse {
    ok: boolean;
    message?: string;
    error?: string;
}

export class CommitMessageGenerator {
    private binaryResolver: BinaryResolver;
    private isGenerating: boolean = false;

    constructor(context: vscode.ExtensionContext) {
        this.binaryResolver = new BinaryResolver(context);
    }

    async generate() {
        if (this.isGenerating) {
            vscode.window.showWarningMessage('Already generating a commit message.');
            return;
        }

        const gitExtension = vscode.extensions.getExtension('vscode.git');
        if (!gitExtension) {
            vscode.window.showErrorMessage('Git extension is not available.');
            return;
        }

        const git = gitExtension.isActive ? gitExtension.exports : await gitExtension.activate();
        const api = git.getAPI(1);
        
        if (api.repositories.length === 0) {
            vscode.window.showErrorMessage('No Git repository found.');
            return;
        }

        const repository = api.repositories[0];
        const changes = repository.state.indexChanges;
        const workingChanges = repository.state.workingTreeChanges;

        if (changes.length === 0 && workingChanges.length === 0) {
            vscode.window.showErrorMessage('No changes to commit. Stage your changes first.');
            return;
        }

        const binaryPath = this.binaryResolver.resolveBinaryPath();
        if (!binaryPath) {
            vscode.window.showErrorMessage('Kvist binary was not found.');
            return;
        }

        const validationError = this.binaryResolver.validateBinary(binaryPath);
        if (validationError) {
            vscode.window.showErrorMessage(`Failed to start Kvist: ${validationError}`);
            return;
        }

        this.isGenerating = true;

        await vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: 'Generating commit message with Kvist',
            cancellable: true
        }, async (progress, token) => {
            try {
                progress.report({ message: 'Collecting changes...' });
            const payload = await this.buildPayload(repository);
                
                if (token.isCancellationRequested) {
                    return;
                }

                progress.report({ message: 'Waiting for the model...' });
                const message = await this.callKvist(binaryPath, payload, token);
                
                if (token.isCancellationRequested) {
                    return;
                }

                if (message) {
                    repository.inputBox.value = message;
                }
            } catch (error) {
                const errorMessage = error instanceof Error ? error.message : 'Unknown error';
                vscode.window.showErrorMessage(`Failed to generate commit message: ${errorMessage}`);
            } finally {
                this.isGenerating = false;
            }
        });
    }

    private async buildPayload(repository: any): Promise<string> {
        const diff = await this.getDiff(repository);
        const history = await this.getCommitHistory(repository);

        const payload = {
            files: splitDiffIntoFiles(diff),
            recent_subjects: history
        };

        return JSON.stringify(payload);
    }

    private async getDiff(repository: any): Promise<string> {
        try {
            const root = repository.rootUri.fsPath;
            return await runGit(root, ['diff', '--no-ext-diff', '--binary', 'HEAD', '--']);
        } catch (error) {
            console.error('Failed to get diff:', error);
            return '';
        }
    }

    private async getCommitHistory(repository: any): Promise<string[]> {
        try {
            const log = await repository.log({ maxEntries: 15 });
            return log.map((commit: any) => commit.message.split('\n')[0]).filter(Boolean);
        } catch (error) {
            console.error('Failed to get commit history:', error);
            return [];
        }
    }

    private async callKvist(binaryPath: string, payload: string, token: vscode.CancellationToken): Promise<string | null> {
        let child: ChildProcess | undefined;
        return new Promise((resolve, reject) => {
            child = spawn(binaryPath, ['--commit-message'], {
                stdio: ['pipe', 'pipe', 'pipe']
            });

            let stdout = '';
            let stderr = '';

            child.stdout?.on('data', (data) => {
                stdout += data.toString();
            });

            child.stderr?.on('data', (data) => {
                stderr += data.toString();
            });

            child.on('error', (error) => {
                reject(new Error(`Failed to start Kvist: ${error.message}`));
            });

            child.on('close', (code) => {
                const details = stderr.trim();
                if (token.isCancellationRequested) {
                    resolve(null);
                    return;
                }

                if (code !== 0) {
                    reject(new Error(details || `Kvist exited with code ${code}`));
                    return;
                }

                try {
                    const response: CommitMessageResponse = JSON.parse(stdout.trim());
                    if (response.ok && response.message) {
                        resolve(response.message);
                    } else {
                        reject(new Error(response.error || 'Kvist could not generate a commit message.'));
                    }
                } catch (error) {
                    reject(new Error('Kvist returned an unreadable reply.'));
                }
            });

            token.onCancellationRequested(() => {
                child?.kill();
            });

            // Send payload to stdin
            child.stdin?.write(payload);
            child.stdin?.end();
        });
    }
}

interface CommitFile {
    path: string;
    patch: string;
}

function runGit(cwd: string, args: string[]): Promise<string> {
    return new Promise((resolve, reject) => {
        execFile('git', ['-C', cwd, ...args], { encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 }, (error, stdout, stderr) => {
            if (error && error.code !== 1) {
                reject(new Error(stderr.trim() || error.message));
                return;
            }
            resolve(stdout);
        });
    });
}

function splitDiffIntoFiles(diff: string): CommitFile[] {
    const matches = [...diff.matchAll(/^diff --git a\/(.*?) b\/(.*?)$/gm)];
    if (matches.length === 0 && diff.trim()) {
        return [{ path: '.', patch: diff }];
    }
    return matches.map((match, index) => {
        const start = match.index ?? 0;
        const end = index + 1 < matches.length ? (matches[index + 1].index ?? diff.length) : diff.length;
        return { path: path.normalize(match[2]), patch: diff.slice(start, end) };
    });
}
