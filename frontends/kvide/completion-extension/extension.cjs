'use strict';
// kvide inline completion provider backed by `kvist-core --complete-server`: one suggestion per
// request, the same model call the IDE plugins use. kvide shows it as ghost text after the
// cursor (Tab accepts, Esc dismisses), as VS Code does.
const vscode = require('vscode');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

// kvide ends an inline request after fifteen seconds, activation included; a silent miss beats
// an error notification on every typing pause. A cold core plus the model takes about five.
const ANSWER_TIMEOUT_MS = 12000;

function resolveCore() {
    if (process.env.KVIST_CORE) return process.env.KVIST_CORE;
    try {
        return JSON.parse(fs.readFileSync(path.join(__dirname, 'core.json'), 'utf8')).core;
    } catch (error) {
        if (error.code !== 'ENOENT') throw error;
        throw new Error('kvist-core is unknown: set KVIST_CORE or run scripts/installer/install_kvide.py');
    }
}

class CoreServer {
    constructor(core, root) {
        this.closed = false;
        this.nextId = 1;
        this.pending = new Map();
        this.buffer = '';
        this.child = spawn(core, ['--complete-server', '--workspace', root], { stdio: ['pipe', 'pipe', 'ignore'] });
        this.child.stdout.setEncoding('utf8');
        this.child.stdout.on('data', chunk => this.onData(chunk));
        this.child.stdin.on('error', error => this.fail(error));
        this.child.on('error', error => this.fail(error));
        this.child.on('exit', () => this.fail(new Error('kvist-core exited')));
    }

    ask(payload) {
        const id = this.nextId++;
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                this.pending.delete(id);
                this.send({ cancel: id });
                resolve('');
            }, ANSWER_TIMEOUT_MS);
            this.pending.set(id, { text: '', resolve, reject, timer });
            this.send({ id, ...payload });
        });
    }

    send(message) {
        if (!this.closed) this.child.stdin.write(JSON.stringify(message) + '\n');
    }

    onData(chunk) {
        const lines = (this.buffer + chunk).split('\n');
        this.buffer = lines.pop();
        for (const line of lines) this.onMessage(JSON.parse(line));
    }

    onMessage(message) {
        const entry = this.pending.get(message.id);
        if (!entry) return;
        if (typeof message.delta === 'string') {
            entry.text += message.delta;
            return;
        }
        this.pending.delete(message.id);
        clearTimeout(entry.timer);
        if (message.done) entry.resolve(entry.text);
        else entry.reject(new Error(message.error || 'kvist-core returned an unknown reply'));
    }

    fail(error) {
        this.closed = true;
        for (const entry of this.pending.values()) {
            clearTimeout(entry.timer);
            entry.reject(error);
        }
        this.pending.clear();
    }

    close() {
        this.closed = true;
        this.child.kill();
    }
}

exports.activate = function activate(context) {
    let server;
    const provider = {
        async provideInlineCompletionItems(document, position) {
            const root = vscode.workspace.rootPath;
            const relative = path.relative(root, document.fileName);
            if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) return [];
            if (!server || server.closed) server = new CoreServer(resolveCore(), root);
            const text = document.getText();
            const offset = document.offsetAt(position);
            const completion = await server.ask({
                file_path: relative.split(path.sep).join('/'),
                language: document.languageId,
                prefix: text.slice(0, offset),
                suffix: text.slice(offset),
            });
            if (!completion.trim()) return [];
            return [new vscode.InlineCompletionItem(completion, new vscode.Range(position, position))];
        },
    };
    context.subscriptions.push(
        vscode.languages.registerInlineCompletionItemProvider('*', provider),
        { dispose: () => server && server.close() },
    );
};
