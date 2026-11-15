import { api } from '/assets/utils/api.js';
import { COMMANDS, parseCommand } from '/assets/features/commands.js';
import { closeMenu, openMenu } from './menu.js';
import { t } from './i18n.js';

// What a person does on the computer's agent from the phone: the "/" commands, as a menu and as typed text.
// The core's own commands do the work (model, reasoning, role), so a change is saved the way the other
// interfaces save it, and the answer comes back as a command_result event.

const PHONE = ['model', 'reasoning', 'role', 'diff', 'new', 'stop'];
const KIND = { model: 'model', reasoning: 'effort', role: 'role' };
const effortLabel = value => (value === 'false' ? 'off' : value);

// ctx: selection(), hello() (what the core said about itself), busy(), createSession(), stop(), toast(text).
export function createControls(ctx) {
    const current = {
        model: () => ctx.hello().models?.fast || ctx.hello().model || '',
        reasoning: () => effortLabel(String(ctx.hello().effort ?? '')),
        role: () => ctx.hello().role || '',
    };

    function main() {
        const rows = COMMANDS.filter(command => PHONE.includes(command.name)).map(command => ({
            label: `/${command.name}`,
            hint: current[command.name]?.() || '',
            onPick: () => run(command.name),
        }));
        openMenu({ title: t('menuTitle'), rows });
    }

    async function choose(command) {
        const selection = ctx.selection();
        if (!selection) return;
        if (ctx.busy()) {
            ctx.toast(t('busyNow'));
            return;
        }
        const kind = KIND[command];
        openMenu({ title: t(command), note: t('loading'), back: main });
        const info = await api.configuration(selection.serverId);
        const hello = ctx.hello();
        if (kind === 'model') {
            await chooseModel(selection, info, hello);
            return;
        }
        const entries = kind === 'effort' ? info.effort_info : info.roles;
        const now = kind === 'effort' ? String(hello.effort ?? info.effort ?? '') : hello.role || info.role || 'auto';
        openMenu({
            title: t(command), back: main,
            rows: entries.map(entry => ({
                label: kind === 'effort' ? effortLabel(entry.value) : entry.value, hint: entry.description, checked: entry.value === now,
                onPick: () => apply(selection, kind, [entry.value]),
            })),
        });
    }

    async function chooseModel(selection, info, hello) {
        const provider = [...info.providers, ...info.custom_providers].find(item => item.id === (hello.provider || info.provider));
        const builtIn = (provider?.options || []).map(model => (typeof model === 'string' ? { value: model } : model));
        let models = builtIn, note = t('modelHint');
        if (provider?.credential_fields?.length && provider.credential_fields.every(field => field.present)) {
            try {
                const result = await api.modelCatalog(selection.serverId, { provider: provider.id, base_url: '', models: provider.defaults, effort: info.effort, credentials: {} });
                models = result.models;
            } catch (error) {
                console.warn('model catalog failed', error);
                note = t('modelsFailed');
            }
        }
        const now = current.model();
        const smart = hello.models?.smart && hello.models.smart !== hello.models.fast ? hello.models.smart : null;
        openMenu({
            title: t('model'), back: main, note, filter: models.length > 8,
            rows: models.map(model => ({
                label: model.label || model.value, checked: model.value === now,
                onPick: () => apply(selection, 'model', [model.value, smart || model.value]),
            })),
        });
    }

    async function apply(selection, kind, args) {
        closeMenu();
        // The core answers with command_result, which the page shows and folds into what it displays.
        await api.command(selection, kind === 'model' ? 'models' : kind, args);
    }

    async function diff() {
        const selection = ctx.selection();
        if (!selection) return;
        openMenu({ title: '/diff', note: t('loading'), back: main });
        const result = await api.changes(selection);
        openMenu({ title: '/diff', text: result.report || t('noDiff'), back: main });
    }

    function run(name) {
        if (name === 'model' || name === 'reasoning' || name === 'role') return choose(name);
        if (name === 'diff') return diff();
        if (name === 'new') {
            closeMenu();
            return ctx.createSession();
        }
        if (name === 'stop') {
            closeMenu();
            return ctx.stop();
        }
        ctx.toast(t('computerOnly'));
        return Promise.resolve();
    }

    // Typed text: a known "/command" runs here; an unknown one is refused, and anything else is a message.
    function typed(text) {
        const parsed = parseCommand(text);
        if (!parsed) return null;
        if (parsed.unknown) {
            ctx.toast(t('unknownCommand', parsed.unknown));
            return Promise.resolve();
        }
        return run(parsed.command.name);
    }

    return { main, run, typed };
}
