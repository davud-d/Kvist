import * as vscode from 'vscode';
import * as path from 'path';

const EXCLUDE = '**/{node_modules,.git,.kvist,.venv,venv,__pycache__,dist,build,out}/**';
const FILE_LIMIT = 5000;
const RESULT_LIMIT = 50;

interface FileItem extends vscode.QuickPickItem {
    relativePath: string;
}

function toItem(relativePath: string): FileItem {
    const dir = path.dirname(relativePath);
    return { label: path.basename(relativePath), description: dir === '.' ? '' : dir, relativePath, alwaysShow: true };
}

// The agent may not read .env files, so offering them would only invite a refusal.
function isAttachable(relativePath: string): boolean {
    return !path.basename(relativePath).startsWith('.env');
}

function openTabPaths(): string[] {
    return vscode.window.tabGroups.all
        .flatMap((group) => group.tabs)
        .map((tab) => (tab.input instanceof vscode.TabInputText ? tab.input.uri : undefined))
        .filter((uri): uri is vscode.Uri => uri?.scheme === 'file' && vscode.workspace.getWorkspaceFolder(uri) !== undefined)
        .map((uri) => vscode.workspace.asRelativePath(uri, false))
        .filter(isAttachable);
}

function rank(paths: string[], query: string): string[] {
    const needle = query.toLowerCase();
    const hits = paths.filter((item) => item.toLowerCase().includes(needle));
    const byName = (item: string) => (path.basename(item).toLowerCase().includes(needle) ? 0 : 1);
    return hits.sort((a, b) => byName(a) - byName(b) || a.length - b.length).slice(0, RESULT_LIMIT);
}

// Only the chosen paths leave this function: the model gets names, not contents.
export async function pickWorkspaceFiles(): Promise<string[]> {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) {
        return [];
    }
    const uris = await vscode.workspace.findFiles(new vscode.RelativePattern(folder, '**/*'), EXCLUDE, FILE_LIMIT);
    const all = uris.map((uri) => vscode.workspace.asRelativePath(uri, false)).filter(isAttachable);

    const selected = new Set<string>();
    const picker = vscode.window.createQuickPick<FileItem>();
    picker.canSelectMany = true;
    picker.placeholder = 'Attach files by name (their contents are not sent)';

    const show = (query: string) => {
        const found = query ? rank(all, query) : [...new Set([...openTabPaths(), ...all.slice(0, RESULT_LIMIT)])].slice(0, RESULT_LIMIT);
        const paths = [...new Set([...selected, ...found])];
        const items = paths.map(toItem);
        picker.items = items;
        picker.selectedItems = items.filter((item) => selected.has(item.relativePath));
    };

    picker.onDidChangeValue(show);
    // Items hidden by a new query keep their state: only the visible ones can change.
    picker.onDidChangeSelection((chosen) => {
        const chosenPaths = new Set(chosen.map((item) => item.relativePath));
        for (const item of picker.items) {
            if (chosenPaths.has(item.relativePath)) {
                selected.add(item.relativePath);
            } else {
                selected.delete(item.relativePath);
            }
        }
    });
    show('');

    return new Promise((resolve) => {
        let accepted = false;
        picker.onDidAccept(() => {
            accepted = true;
            picker.hide();
        });
        picker.onDidHide(() => {
            resolve(accepted ? [...selected] : []);
            picker.dispose();
        });
        picker.show();
    });
}
