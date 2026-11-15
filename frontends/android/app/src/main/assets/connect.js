import { isWinter } from '/assets/mobile/season.js';
import { lang, t } from '/assets/mobile/i18n.js';
import { createMascot } from '/assets/mobile/mascot.js';
import { startSnow } from '/assets/mobile/snow.js';

// The connect screen of the Android app: it lists the computers that run Kvist on this network (system,
// host name, user), and a tap asks that computer to trust this phone: the computer asks its owner, once.
// The app (Kotlin) owns the network; this page only draws. `window.KvistNative` exists only inside the app.

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
const failureDetail = new URLSearchParams(location.search).get('detail');
const OS_TAGS = { linux: 'LINUX', macos: 'MACOS', windows: 'WIN' };

let scanning = false;
let asking = false;
let autoTried = false;

const send = message => native.postMessage(JSON.stringify(message));
const say = (text, detail = '') => {
    $('bubble').textContent = text;
    // The network's own words, small: what a person reads out to whoever is helping.
    $('detail').textContent = detail;
    $('detail').hidden = !detail;
};
const part = (tag, cls, text) => { const node = document.createElement(tag); node.className = cls; node.textContent = text; return node; };

function computerRow(computer) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'session computer';
    const lines = document.createElement('span');
    lines.className = 'computer-lines';
    lines.append(
        part('span', 'session-title', computer.name),
        part('span', 'computer-sub', [computer.os_name, computer.user].filter(Boolean).join(' · ')),
    );
    row.append(
        part('span', `os-tag ${computer.os}`, OS_TAGS[computer.os] || '?'),
        lines,
        part('span', 'session-state', computer.saved ? t('savedMark') : computer.host),
    );
    row.onclick = () => pair(computer, false);
    return row;
}

function showComputers(computers) {
    scanning = false;
    mascot.setMood('idle');
    $('computers').replaceChildren(...computers.map(computerRow));
    $('hint').hidden = computers.length > 0;
    // Why the app is here (the computer forgot this phone) outlasts the result of the search.
    const why = failure === 'forgotten' ? t('forgotten') : failure === 'unreachable' ? t('unreachable') : '';
    say(why || (computers.length ? t('foundN', computers.length) : t('nothing')), failure === 'unreachable' ? failureDetail || '' : '');
    // The same computer under a new address (the router handed out another one): no need to ask again.
    const known = computers.find(computer => computer.saved);
    if (known && !failure && !autoTried) {
        autoTried = true;
        pair(known, true);
    }
}

function scan() {
    if (scanning || asking) return;
    scanning = true;
    $('computers').replaceChildren();
    $('hint').hidden = true;
    say(t('scanning'));
    mascot.setMood('work');
    send({ op: 'scan' });
}

// `auto`: the app is reopening a computer it already knows, so it only uses the token it has and never makes
// a window appear on that computer.
function pair(computer, auto) {
    if (asking) return;
    asking = true;
    $('manual').hidden = true;
    say(auto ? t('connecting') : t('asking', computer.name));
    mascot.setMood('wait');
    send({ op: 'pair', id: computer.id || '', name: computer.name, host: computer.host, port: computer.port, auto });
}

const FAILURES = {
    denied: 'denied', unavailable: 'unavailable', busy: 'busy', cooldown: 'cooldown', forgotten: 'forgotten', expired: 'expired',
};

function onMessage(message) {
    if (message.type === 'saved') {
        scan();
    } else if (message.type === 'found') {
        showComputers(message.computers);
    } else if (message.type === 'pair-failed') {
        asking = false;
        mascot.setMood('idle');
        say(t(FAILURES[message.reason] || 'unreachable'), message.detail || '');
    }
}

function submitManual() {
    const [host, port] = $('manual-address').value.trim().split(':');
    if (host) pair({ id: '', name: host, host, port: Number(port) || 9671 }, false);
}

$('tagline').textContent = t('tagline');
$('hint-run').textContent = t('hintRun');
$('hint-network').textContent = t('hintNetwork');
$('rescan').textContent = t('rescan');
$('show-manual').textContent = t('manual');
$('manual-go').textContent = t('connect');
$('manual-address').placeholder = t('address');
$('hint-command').textContent = t('hintCommand');

$('rescan').onclick = scan;
$('show-manual').onclick = () => { $('manual').hidden = !$('manual').hidden; };
$('manual-go').onclick = submitManual;

if (native) {
    native.onmessage = event => onMessage(JSON.parse(event.data));
    if (failure === 'forgotten') say(t('forgotten'));
    send({ op: 'saved' });
} else {
    say('Open this page in the Kvist app.');
}
