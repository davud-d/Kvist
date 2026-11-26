import * as http from 'http';
import { randomBytes } from 'crypto';
import type { AddressInfo } from 'net';

// Signing in to Kvist Cloud for the panel's framed cloud page (cloud/backend/kvist_cloud/appauth.py). GitHub's page
// refuses frames, so the system browser signs in; the cloud hands a short-lived code to a listener on this computer
// (RFC 8252 loopback), and the framed page trades the code, with the nonce only this extension knows, for its session.

const WAIT_MS = 5 * 60_000;
const DONE_PAGE = `<!doctype html><meta charset="utf-8"><title>Kvist Cloud</title>
<body style="font:16px system-ui;background:#0a0a0b;color:#e8e8ea;display:grid;place-items:center;height:90vh">
<p>Signed in. Go back to the IDE. / Вход выполнен, вернись в IDE.</p></body>`;

/**
 * Opens the cloud's sign-in in the system browser and resolves to the address the framed page loads to get its
 * session; rejects when the browser does not come back within five minutes.
 */
export async function signInToCloud(cloudOrigin: string, openBrowser: (url: string) => Thenable<boolean>): Promise<string> {
    const nonce = randomBytes(32).toString('base64url');
    const server = http.createServer();
    try {
        await new Promise<void>((resolve, reject) => {
            server.once('error', reject);
            server.listen(0, '127.0.0.1', resolve);
        });
        const port = (server.address() as AddressInfo).port;
        const code = waitForCode(server);
        await openBrowser(`${cloudOrigin}/auth/app/start?${new URLSearchParams({ nonce, port: String(port) })}`);
        const query = new URLSearchParams({ code: await code, nonce, next: '/m/' });
        return `${cloudOrigin}/auth/app/exchange?${query}`;
    } finally {
        server.close();
    }
}

function waitForCode(server: http.Server): Promise<string> {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('The sign-in was not finished in time. / Вход не завершён вовремя.')), WAIT_MS);
        server.on('close', () => clearTimeout(timer));
        server.on('request', (request: http.IncomingMessage, response: http.ServerResponse) => {
            const url = new URL(request.url ?? '/', 'http://127.0.0.1');
            const code = url.searchParams.get('code');
            if (url.pathname !== '/signed-in' || !code) {
                response.writeHead(404).end();
                return;
            }
            response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }).end(DONE_PAGE);
            clearTimeout(timer);
            resolve(code);
        });
    });
}
