import { isWinter } from '/assets/mobile/season.js';
import { lang, t } from '/assets/mobile/i18n.js';
import { createMascot } from '/assets/mobile/mascot.js';
import { startSnow } from '/assets/mobile/snow.js';

// The connect screen of the Android app: it finds the computers that run `kvist-web --lan`, takes the
// code from the person and hands both to the app, which opens that computer's page. The app (Kotlin)
// owns the network; this page only draws. `window.KvistNative` exists only inside the app.

const WINTER_BG = '#0e1a33';
const winter = isWinter(new Date(), location.search);
document.documentElement.dataset.season = winter ? 'winter' : 'default';
document.documentElement.lang = lang;
if (winter) {
    document.querySelector('meta[name=theme-color]').content = WINTER_BG;
    startSnow(document.getElementById('snow'));
}

const $ = id => document.getElementById(id);
const native = window.KvistNative;
const mascot = createMascot($('mascot'), { winter });
const failure = new URLSearchParams(location.search).get('err');

let saved = null;
let chosen = null;
let scanning = false;
let autoTried = false;

const send = message => native.postMessage(JSON.stringify(message));
const say = text => { $('bubble').textContent = text; };

function computerRow(computer) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'session';
    const dot = document.createElement('span');
    dot.className = 'dot finished';
    const name = document.createElement('span');
    name.className = 'session-title';
    name.textContent = computer.name;
    const address = document.createElement('span');
    address.className = 'session-state';
    address.textContent = computer.saved ? t('savedMark') : computer.host;
    row.append(dot, name, address);
    row.onclick = () => choose(computer);
    return row;
}

function showComputers(computers) {
    scanning = false;
    mascot.setMood('idle');
    $('computers').replaceChildren(...computers.map(computerRow));
    $('hint').hidden = computers.length > 0;
    say(computers.length ? t('foundN', computers.length) : t('nothing'));
    // The same computer under a new address (the router handed out another one): no need to ask again.
    const known = computers.find(computer => computer.saved);
    if (known && failure !== 'code' && !autoTried) {
        autoTried = true;
        pair(known, '');
    }
}

function scan() {
    if (scanning) return;
    scanning = true;
    $('computers').replaceChildren();
    $('hint').hidden = true;
    $('code-sheet').hidden = true;
    say(t('scanning'));
    mascot.setMood('work');
    send({ op: 'scan' });
}

function choose(computer) {
    chosen = computer;
    $('manual').hidden = true;
    $('code-title').textContent = t('codeTitle', computer.name);
    $('code-error').hidden = true;
    $('code').value = '';
    $('code-sheet').hidden = false;
    $('code').focus();
}

function pair(computer, code) {
    say(t('connecting'));
    mascot.setMood('work');
    send({ op: 'pair', host: computer.host, port: computer.port, name: computer.name, code });
}

function shown(message) {
    $('code-error').textContent = message;
    $('code-error').hidden = false;
}

function onMessage(message) {
    if (message.type === 'saved') {
        saved = message.saved;
        if (saved && failure === 'unreachable') say(t('lostSaved', saved.name));
        scan();
    } else if (message.type === 'found') {
        showComputers(message.computers);
    } else if (message.type === 'pair-failed') {
        mascot.setMood('idle');
        const text = message.reason === 'code' ? t('wrongCode') : t('unreachable');
        if (!$('code-sheet').hidden) shown(text);
        else say(text);
    }
}

function submitManual() {
    const [host, port] = $('manual-address').value.trim().split(':');
    if (!host) return;
    $('manual').hidden = true;
    choose({ name: host, host, port: Number(port) || 9671 });
}

$('tagline').textContent = t('tagline');
$('hint-run').textContent = t('hintRun');
$('hint-network').textContent = t('hintNetwork');
$('code-hint').textContent = t('codeHint');
$('code-cancel').textContent = t('cancel');
$('code-go').textContent = t('connect');
$('rescan').textContent = t('rescan');
$('show-manual').textContent = t('manual');
$('manual-go').textContent = t('connect');
$('manual-address').placeholder = t('address');

$('rescan').onclick = scan;
$('show-manual').onclick = () => { $('code-sheet').hidden = true; $('manual').hidden = !$('manual').hidden; };
$('manual-go').onclick = submitManual;
$('code-cancel').onclick = () => { $('code-sheet').hidden = true; };
$('code-go').onclick = () => { if (chosen) pair(chosen, $('code').value); };
$('code').addEventListener('input', () => { $('code').value = $('code').value.toUpperCase(); });
$('code').addEventListener('keydown', event => { if (event.key === 'Enter') $('code-go').click(); });

if (native) {
    native.onmessage = event => onMessage(JSON.parse(event.data));
    send({ op: 'saved' });
} else {
    say('Open this page in the Kvist app.');
}
