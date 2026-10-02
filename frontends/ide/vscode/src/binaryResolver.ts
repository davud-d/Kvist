import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { execFileSync } from 'child_process';
import { SupportedOs, currentOs, isMacos, isPosix, relativeBinarySegments } from './platform';

export class BinaryResolver {
    private extensionPath: string;

    constructor(context: vscode.ExtensionContext) {
        this.extensionPath = context.extensionPath;
    }

    getCurrentOs(): SupportedOs | null {
        return currentOs(process.platform, process.arch);
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

        const binaryPath = path.join(this.extensionPath, ...relativeBinarySegments(os));

        if (!fs.existsSync(binaryPath)) {
            return null;
        }

        return binaryPath;
    }

    // Gatekeeper refuses a quarantined binary that is not notarized. Nothing is wrong when the
    // mark is absent, which is the usual case, so a failure of xattr is not reported.
    private clearQuarantine(binaryPath: string): void {
        try {
            execFileSync('xattr', ['-d', 'com.apple.quarantine', binaryPath], { stdio: 'ignore' });
        } catch {
            // xattr exits non-zero when the attribute is not there.
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

            const os = this.getCurrentOs();

            // On Linux and macOS, check if executable
            if (isPosix(os)) {
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

            if (isMacos(os)) {
                this.clearQuarantine(binaryPath);
            }

            return null;
        } catch (error) {
            return `Failed to validate binary: ${error}`;
        }
    }
}
