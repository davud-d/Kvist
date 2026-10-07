// Bytes of the frozen remote mode (SSH servers) in the web client, by the same rule as the size limit
// (UTF-8 bytes of .js, .css and .html in kvist_web/client/). Run from the repo root: node docs/web-onboarding/remote-size.mjs
import { readFileSync } from 'node:fs';
const client = 'frontends/web-local/kvist_web/client/';
const lines = file => readFileSync(client + file, 'utf8').split('\n');
const bytes = text => Buffer.byteLength(text, 'utf8');
const rows = [];
// connections.js: the server switcher, server list, connect dialog, trust and connection log (from SHOW_SERVERS up to
// addWorkspace), and showSettings, which only synchronizes configuration to a remote server.
{
  const all = lines('features/connections.js');
  const from = all.findIndex(l => l.startsWith('export const SHOW_SERVERS'));
  const to = all.findIndex(l => l.startsWith('export function addWorkspace'));
  const settings = all.findIndex(l => l.startsWith('export async function showSettings'));
  rows.push(['connections.js: servers, connect, trust, log', bytes(all.slice(from, to).join('\n') + '\n')]);
  rows.push(['connections.js: configuration sync (Settings)', bytes(all.slice(settings).join('\n'))]);
}
// api.js: the calls only the remote mode makes.
{
  const remote = /^\s*(sshAliases|addServer|connectServer|configSyncPreview|syncConfig):/;
  rows.push(['api.js: server and sync calls', bytes(lines('utils/api.js').filter(l => remote.test(l)).join('\n') + '\n')]);
}
// app.js: lines that wire the remote mode.
{
  const remote = /mountServerSwitcher|showServers|showServerModal|handleConnectionLog|Switch server|Connect server/;
  rows.push(['app.js: wiring', bytes(lines('app.js').filter(l => remote.test(l)).join('\n') + '\n')]);
}
// styles.css: rules whose selector names a remote-only part.
{
  const css = readFileSync(client + 'styles.css', 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  const remote = /connection-tabs|server-|runtime-button|active-server|connection-log|connection-stage|connection-progress|credential-list|trust_required|ssh-|nav-group-server|session-server/;
  const rules = css.match(/[^{}]+\{[^{}]*\}/g) || [];
  rows.push(['styles.css: remote-only rules', rules.filter(rule => remote.test(rule.split('{')[0])).reduce((sum, rule) => sum + bytes(rule), 0)]);
}
const total = rows.reduce((sum, [, n]) => sum + n, 0);
for (const [name, n] of rows) console.log(`${String(n).padStart(6)}  ${name}`);
console.log(`${String(total).padStart(6)}  total`);
