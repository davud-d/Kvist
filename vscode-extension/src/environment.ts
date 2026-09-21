const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/

export interface CleanEnvironment {
    valid: Record<string, string>;
    // Names that were refused, so the caller can say so instead of dropping them silently.
    rejected: string[];
}

// Settings can be edited by hand, so nothing about their shape is assumed. A name that is
// not a plain identifier, or a value that is not text, never reaches the child process.
export function splitEnvironment(value: unknown): CleanEnvironment {
    const result: CleanEnvironment = { valid: {}, rejected: [] };
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
        return result;
    }
    for (const [name, item] of Object.entries(value)) {
        if (NAME.test(name) && typeof item === 'string' && !item.includes('\0')) {
            result.valid[name] = item;
        } else {
            result.rejected.push(name);
        }
    }
    return result;
}
