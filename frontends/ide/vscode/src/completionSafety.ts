import { realpathSync } from 'fs';

/** Deliberately includes templates/backups and protected directory components. */
export function isEnvCompletionPath(filePath: string): boolean {
    return filePath.replace(/\\/g, '/').split('/').some(part => part.toLowerCase().startsWith('.env'));
}

/** Resolve metadata only, never open the document. Unknown local paths fail closed. */
export function canReadCompletionDocument(
    filePath: string,
    readText: () => string,
    resolvePath: (path: string) => string = realpathSync
): string | undefined {
    if (isEnvCompletionPath(filePath)) { return undefined; }
    try {
        if (isEnvCompletionPath(resolvePath(filePath))) { return undefined; }
    } catch {
        return undefined;
    }
    return readText();
}
