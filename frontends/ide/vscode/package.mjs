// Packages the extension with `vsce package`.
//   --target <vsce target>  keeps only that platform's binary from bin/ (a package for one
//                           platform should not carry the other platforms' 120 MB binaries)
//   --proposals             adds the `inlineCompletionsAdditions` API proposal to the manifest.
//                           The Marketplace refuses manifests that list proposed APIs, so
//                           package.json does not; that build is for GitHub Releases only.
// Every other argument goes to `vsce package`.
import { copyFileSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const MANIFEST = 'package.json';
const MANIFEST_BACKUP = '.package.json.orig';
const IGNORE = '.vscodeignore';
const TARGET_IGNORE = '.vscodeignore.target';
const PROPOSALS = ['inlineCompletionsAdditions'];
// The folder under bin/ that bin/ holds for each target (see src/platform.ts).
const BIN_DIRS = { 'win32-x64': 'win', 'linux-x64': 'linux', 'darwin-arm64': 'mac-arm64', 'darwin-x64': 'mac-x64' };

const args = process.argv.slice(2);
const proposals = args.includes('--proposals');
const rest = args.filter((arg) => arg !== '--proposals');
const target = rest.includes('--target') ? rest[rest.indexOf('--target') + 1] : undefined;
if (target && !(target in BIN_DIRS)) {
    console.error(`Unknown target "${target}". Known: ${Object.keys(BIN_DIRS).join(', ')}`);
    process.exit(2);
}

const manifest = JSON.parse(readFileSync(MANIFEST, 'utf8'));
const hasOutput = rest.includes('-o') || rest.includes('--out');
const outputName = `kvist-${manifest.version}${target ? `-${target}` : ''}${proposals ? '-proposals' : ''}.vsix`;
const vsceArgs = ['vsce', 'package', '--no-dependencies', ...(hasOutput ? [] : ['-o', outputName]), ...rest];

if (target) {
    const otherPlatforms = Object.values(BIN_DIRS).filter((dir) => dir !== BIN_DIRS[target]);
    writeFileSync(TARGET_IGNORE, readFileSync(IGNORE, 'utf8') + '\n' + otherPlatforms.map((dir) => `bin/${dir}/**`).join('\n') + '\n');
    vsceArgs.push('--ignoreFile', TARGET_IGNORE);
}
if (proposals) {
    copyFileSync(MANIFEST, MANIFEST_BACKUP);
    writeFileSync(MANIFEST, JSON.stringify({ ...manifest, enabledApiProposals: PROPOSALS }, null, 2) + '\n');
}

let status = 1;
try {
    status = spawnSync('npx', vsceArgs, { stdio: 'inherit', shell: process.platform === 'win32' }).status ?? 1;
} finally {
    if (proposals) renameSync(MANIFEST_BACKUP, MANIFEST);
    if (target) rmSync(TARGET_IGNORE, { force: true });
}
process.exit(status);
