// Packages the extension with the `inlineCompletionsAdditions` API proposal enabled.
// The Marketplace refuses manifests that list proposed APIs, so package.json does not;
// this build is for GitHub Releases and registries that accept it. Extra arguments go to
// `vsce package`, for example `--target linux-x64`.
import { copyFileSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const MANIFEST = 'package.json';
const BACKUP = '.package.json.orig';
const PROPOSALS = ['inlineCompletionsAdditions'];

const args = process.argv.slice(2);
const target = args.includes('--target') ? args[args.indexOf('--target') + 1] : undefined;
const manifest = JSON.parse(readFileSync(MANIFEST, 'utf8'));
const out = args.includes('-o')
    ? []
    : ['-o', `kvist-${manifest.version}${target ? `-${target}` : ''}-proposals.vsix`];

copyFileSync(MANIFEST, BACKUP);
let status = 1;
try {
    manifest.enabledApiProposals = PROPOSALS;
    writeFileSync(MANIFEST, JSON.stringify(manifest, null, 2) + '\n');
    status = spawnSync('npx', ['vsce', 'package', '--no-dependencies', ...out, ...args], { stdio: 'inherit', shell: process.platform === 'win32' }).status ?? 1;
} finally {
    renameSync(BACKUP, MANIFEST);
}
process.exit(status);
