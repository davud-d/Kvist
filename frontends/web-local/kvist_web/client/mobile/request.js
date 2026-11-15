import { el } from '/assets/utils/ui.js';
import { choiceOptions, parseUnifiedDiff } from '/assets/features/format.js';
import { t } from './i18n.js';

// The sheet above the composer for a request that waits for the person: a command or an overwrite to
// allow, a choice, or a text. The safe answer (Deny) comes first. All request text goes in through
// textContent.

const DIFF_ROWS = 40;

function button(cls, text, onclick) {
    const node = el('button', `btn ${cls}`, text);
    node.type = 'button';
    node.onclick = onclick;
    return node;
}

function diffView(diff) {
    const rows = parseUnifiedDiff(diff);
    if (!rows.length) return el('pre', 'subject-code', diff || '');
    const box = el('div', 'diff');
    for (const row of rows.slice(0, DIFF_ROWS)) {
        const mark = row.kind === 'add' ? '+' : row.kind === 'del' ? '-' : ' ';
        box.append(el('div', `diff-row ${row.kind}`, `${mark} ${row.text}`));
    }
    if (rows.length > DIFF_ROWS) box.append(el('div', 'diff-more', t('moreLines', rows.length - DIFF_ROWS)));
    return box;
}

function subjectView(subject) {
    if (!subject || typeof subject !== 'object') return null;
    const box = el('div', 'subject');
    if (subject.kind === 'command') {
        box.append(el('div', 'subject-label', t('command')), el('pre', 'subject-code', subject.command || ''));
        if (subject.reason) box.append(el('p', 'subject-reason', subject.reason));
    } else if (subject.kind === 'overwrite') {
        box.append(el('div', 'subject-label', `${t('overwrite')} ${subject.path || ''}`), diffView(subject.diff));
    }
    return box;
}

function choiceView(block, options) {
    const group = el('div', 'choices');
    const current = options.some(option => option.value === block.currentValue) ? block.currentValue : options[0].value;
    for (const option of options) {
        const label = el('label', 'choice');
        const radio = el('input');
        radio.type = 'radio';
        radio.name = `choice-${block.requestId}`;
        radio.value = option.value;
        radio.checked = option.value === current;
        label.append(radio, el('span', '', option.label));
        group.append(label);
    }
    return { node: group, value: () => group.querySelector('input:checked')?.value ?? '' };
}

// Builds the sheet for the unanswered input block; respond(response) sends the answer: a confirm answers
// {approved, remember?}, every other kind {value}.
export function renderRequest(sheet, block, respond) {
    let sent = false;
    // One answer per request: a second tap while the first is on its way would answer twice.
    const once = response => () => {
        if (sent) return;
        sent = true;
        sheet.classList.add('busy');
        respond(response());
    };
    sheet.replaceChildren(el('h3', 'sheet-title', block.title || t('needsInput')));
    const text = block.body || block.helpText;
    if (text) sheet.append(el('p', 'sheet-text', text));
    const subject = subjectView(block.subject);
    if (subject) sheet.append(subject);
    const actions = el('div', 'actions');

    if (block.inputKind === 'confirm') {
        const remember = block.rememberPattern ? el('input') : null;
        actions.append(
            button('danger', t('deny'), once(() => ({ approved: false }))),
            button('primary', t('allow'), once(() => ({ approved: true, ...(remember?.checked ? { remember: true } : {}) }))),
        );
        if (remember) {
            remember.type = 'checkbox';
            const label = el('label', 'remember');
            label.append(remember, el('span', '', t('remember')));
            sheet.append(label);
        }
        sheet.append(actions);
        return;
    }
    const options = block.inputKind === 'choice' ? choiceOptions({ options: block.options }) : [];
    if (options.length) {
        const list = choiceView(block, options);
        actions.append(button('primary', t('ok'), once(() => ({ value: list.value() }))));
        sheet.append(list.node, actions);
        return;
    }
    const field = el('input', 'field');
    field.type = block.inputKind === 'secret' ? 'password' : 'text';
    field.autocomplete = 'off';
    field.value = block.currentValue || '';
    actions.append(button('primary', t('ok'), once(() => ({ value: field.value }))));
    sheet.append(field, actions);
}
