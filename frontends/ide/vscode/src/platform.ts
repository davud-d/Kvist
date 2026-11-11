// Which bundled Kvist binary this machine runs. Kept free of the `vscode` module so the
// mapping can be tested on its own.

export enum SupportedOs {
    WINDOWS = 'windows',
    LINUX = 'linux',
    MACOS_ARM64 = 'macos-arm64',
    MACOS_X64 = 'macos-x64'
}

export function currentOs(platform: string, arch: string): SupportedOs | null {
    switch (platform) {
        case 'win32':
            return SupportedOs.WINDOWS;
        case 'linux':
            return SupportedOs.LINUX;
        case 'darwin':
            // An editor running under Rosetta reports x64 and takes the x64 binary, which Rosetta runs too.
            return arch === 'arm64' ? SupportedOs.MACOS_ARM64 : SupportedOs.MACOS_X64;
        default:
            return null;
    }
}

// Path segments of the binary inside the extension folder.
export function relativeBinarySegments(os: SupportedOs): string[] {
    switch (os) {
        case SupportedOs.WINDOWS:
            return ['bin', 'win', 'kvist.exe'];
        case SupportedOs.LINUX:
            return ['bin', 'linux', 'kvist.bin'];
        case SupportedOs.MACOS_ARM64:
            return ['bin', 'mac-arm64', 'kvist.bin'];
        case SupportedOs.MACOS_X64:
            return ['bin', 'mac-x64', 'kvist.bin'];
    }
}

// The native session program, built beside the core for the same platform. It answers
// `--session-op` without starting the core, which for a frozen binary means without unpacking it.
export function relativeSessionSegments(os: SupportedOs): string[] {
    const segments = relativeBinarySegments(os);
    return [...segments.slice(0, -1), os === SupportedOs.WINDOWS ? 'kvist-session.exe' : 'kvist-session'];
}

// Linux and macOS start the binary through a POSIX shell and need its execute bit.
export function isPosix(os: SupportedOs | null): boolean {
    return os !== null && os !== SupportedOs.WINDOWS;
}

export function isMacos(os: SupportedOs | null): boolean {
    return os === SupportedOs.MACOS_ARM64 || os === SupportedOs.MACOS_X64;
}

export function defaultPosixShell(os: SupportedOs): string {
    return isMacos(os) ? '/bin/zsh' : '/bin/bash';
}
