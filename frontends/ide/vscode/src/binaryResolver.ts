import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';

export enum SupportedOs {
    WINDOWS = 'windows',
    LINUX = 'linux'
}

export class BinaryResolver {
    private extensionPath: string;

    constructor(context: vscode.ExtensionContext) {
        this.extensionPath = context.extensionPath;
    }

    getCurrentOs(): SupportedOs | null {
        const platform = process.platform;
        if (platform === 'win32') {
            return SupportedOs.WINDOWS;
        } else if (platform === 'linux') {
            return SupportedOs.LINUX;
        }
        return null;
    }

    resolveBinaryPath(): string | null {
        const configured = vscode.workspace.getConfiguration('kvist').get<string>('binaryPath', '').trim();
        if (configured) {
            return fs.existsSync(configured) ? configured : null;
        }

        const os = this.getCurrentOs();
        if (!os) {
            return null;
        }

        const relativePath = this.getRelativeBinaryPath(os);
        const binaryPath = path.join(this.extensionPath, relativePath);

        if (!fs.existsSync(binaryPath)) {
            return null;
        }

        return binaryPath;
    }

    private getRelativeBinaryPath(os: SupportedOs): string {
        switch (os) {
            case SupportedOs.WINDOWS:
                return path.join('bin', 'win', 'kvist.exe');
            case SupportedOs.LINUX:
                return path.join('bin', 'linux', 'kvist.bin');
        }
    }

    validateBinary(binaryPath: string): string | null {
        try {
            const stats = fs.statSync(binaryPath);
            
            if (!stats.isFile()) {
                return 'Binary path is not a file.';
            }

            if (stats.size === 0) {
                return 'Binary file is empty.';
            }

            // On Linux, check if executable
            if (this.getCurrentOs() === SupportedOs.LINUX) {
                try {
                    fs.accessSync(binaryPath, fs.constants.X_OK);
                } catch {
                    // Try to make it executable
                    try {
                        fs.chmodSync(binaryPath, 0o755);
                    } catch (error) {
                        return `Failed to make binary executable: ${error}`;
                    }
                }
            }

            return null;
        } catch (error) {
            return `Failed to validate binary: ${error}`;
        }
    }
}
