import { el } from '/assets/utils/ui.js';
import { choiceOptions } from '/assets/features/format.js';

// The panel of an unanswered input_request, pinned above the input field: a heading, the text, the subject
// of the request and the controls of its kind. Tab goes through the controls in DOM order, then on to the
// composer. All request text goes in through textContent.

// The command or the file a request is about, opened so the person can read it before answering. The
// reason of a command goes under its block as a line of text, not into the block.
function subjectDetails(subject) {
    if (!subject || typeof subject !== 'object') return null;
    const details = el('details', 'tool'), summary = el('summary'), pre = el('pre');
    let reason = '';
    details.open = true;
    if (subject.kind === 'command') {
        summary.textContent = 'Command awaiting approval';
        pre.textContent = subject.command || 'No command details were supplied.';
        reason = subject.reason || '';
    } else if (subject.kind === 'overwrite') {
        summary.textContent = subject.path ? `Overwrite ${subject.path}` : 'File overwrite awaiting approval';
        pre.textContent = subject.diff || 'No diff details were supplied.';
    } else {
        summary.textContent = 'Request details';
        pre.textContent = JSON.stringify(subject, null, 2);
    }
    details.append(summary, pre);
    if (reason) details.append(el('p', 'pending-reason', reason));
    return details;
}

function button(cls, text, onclick) {
    const node = el('button', cls, text);
    node.type = 'button';
    node.onclick = onclick;
    return node;
}

// Radio list of the options; the one named by current_value is checked, else the first.
function choiceList(event, options, labelId) {
    const group = el('div', 'pending-choice');
    group.setAttribute('role', 'radiogroup');
    group.setAttribute('aria-labelledby', labelId);
    const current = options.some(option => option.value === String(event.current_value || '')) ? String(event.current_value) : options[0].value;
    for (const option of options) {
        const label = el('label', 'pending-option'), radio = el('input');
        radio.type = 'radio';
        radio.name = `choice-${event.request_id}`;
        radio.value = option.value;
        radio.checked = option.value === current;
        label.append(radio, el('span', '', option.label));
        group.append(label);
    }
    return { node: group, value: () => group.querySelector('input:checked')?.value ?? '' };
}

function textField(event) {
    const input = el('input', 'pending-field');
    input.type = event.kind === 'secret' ? 'password' : 'text';
    input.autocomplete = 'off';
    input.value = event.kind === 'secret' ? '' : event.current_value || '';
    input.setAttribute('aria-label', event.title || 'Response');
    return input;
}

// Builds the panel for `event` and returns its first control. respond(response) sends the answer: a confirm
// answers { approved, remember? }, every other kind { value }.
export function renderPending(panel, event, respond) {
    const title = el('h3', '', event.title || 'Kvist needs input');
    title.id = 'pending-title';
    panel.replaceChildren(title);
    const text = event.body || event.help_text;
    if (text) panel.append(el('p', '', text));
    const subject = subjectDetails(event.subject);
    if (subject) panel.append(subject);
    const actions = el('div', 'pending-actions');
    if (event.kind === 'confirm') {
        // Deny comes first: it is the safe default for focus and for Enter.
        const deny = button('stop-button', 'Deny', () => respond({ approved: false }));
        const remember = event.remember_pattern ? el('input') : null;
        const allow = button('primary-button', 'Allow', () => respond({ approved: true, ...(remember?.checked ? { remember: true } : {}) }));
        actions.append(deny, allow);
        if (remember) {
            remember.type = 'checkbox';
            const label = el('label', 'pending-remember');
            label.append(remember, el('span', '', 'Remember this safe approval'));
            actions.append(label);
        }
        panel.append(actions);
        return deny;
    }
    const options = event.kind === 'choice' ? choiceOptions(event) : [];
    if (options.length) {
        const list = choiceList(event, options, 'pending-title');
        actions.append(button('primary-button', 'Continue', () => respond({ value: list.value() })));
        panel.append(list.node, actions);
        return list.node.querySelector('input:checked');
    }
    const field = textField(event);
    const submit = () => respond({ value: field.value });
    field.addEventListener('keydown', key => {
        if (key.key !== 'Enter' || key.isComposing) return;
        key.preventDefault();
        submit();
    });
    actions.append(field, button('primary-button', 'Continue', submit));
    panel.append(actions);
    return field;
}
