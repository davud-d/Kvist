// Kvist's helpers inside the person's page. Runs in the isolated world "kvist": it shares the DOM
// with the page but not its JavaScript, so page scripts can neither see nor fake globalThis.__kvist.
(() => {
  if (globalThis.__kvist) return;
  const RU = (navigator.language || '').toLowerCase().startsWith('ru');
  const k = { stopped: false, cx: -1, cy: -1 };
  globalThis.__kvist = k;
  let host = null;

  // ---------------------------------------------------------------- page reading
  const refOf = new WeakMap();
  const byRef = new Map();
  let nextRef = 0;
  const refFor = el => {
    let ref = refOf.get(el);
    if (!ref) { ref = 'e' + (++nextRef); refOf.set(el, ref); }
    byRef.set(ref, new WeakRef(el));
    return ref;
  };
  const clean = (text, limit = 80) => {
    const value = String(text || '').replace(/\s+/g, ' ').trim();
    return value.length > limit ? value.slice(0, limit - 1) + '…' : value;
  };
  const ROLES = new Set(['button', 'link', 'checkbox', 'radio', 'switch', 'tab', 'menuitem', 'menuitemcheckbox',
    'menuitemradio', 'option', 'combobox', 'textbox', 'searchbox', 'slider', 'spinbutton', 'treeitem']);
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD', 'META', 'LINK', 'svg', 'SVG', 'PATH']);
  const LEAF = new Set(['A', 'BUTTON', 'INPUT', 'TEXTAREA', 'SELECT', 'SUMMARY', 'OPTION']);
  const BLOCK = new Set(['P', 'DIV', 'LI', 'UL', 'OL', 'TR', 'TD', 'TH', 'SECTION', 'ARTICLE', 'HEADER', 'FOOTER',
    'NAV', 'MAIN', 'ASIDE', 'FORM', 'FIELDSET', 'TABLE', 'BLOCKQUOTE', 'PRE', 'DD', 'DT', 'BR', 'HR', 'FIGCAPTION']);

  const visible = el => el.checkVisibility
    ? el.checkVisibility({ visibilityProperty: true })
    : (s => s.display !== 'none' && s.visibility !== 'hidden')(getComputedStyle(el));

  const roleOf = el => {
    const role = (el.getAttribute('role') || '').split(' ')[0];
    if (ROLES.has(role)) return role;
    const tag = el.tagName;
    if (tag === 'A' && el.hasAttribute('href')) return 'link';
    if (tag === 'BUTTON' || tag === 'SUMMARY') return 'button';
    if (tag === 'SELECT') return 'combobox';
    if (tag === 'TEXTAREA' || el.isContentEditable && el.getAttribute('contenteditable') !== null) return 'textbox';
    if (tag === 'INPUT') {
      const type = (el.type || 'text').toLowerCase();
      if (type === 'hidden') return '';
      if (type === 'checkbox' || type === 'radio') return type;
      if (type === 'range') return 'slider';
      if (['submit', 'button', 'reset', 'image', 'file', 'color'].includes(type)) return 'button';
      return type === 'search' ? 'searchbox' : 'textbox';
    }
    return '';
  };

  // The pointer cursor is how a page says a plain div is clickable; only the outermost one counts.
  const pointerTarget = el => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) return false;
    if (getComputedStyle(el).cursor !== 'pointer') return false;
    const parent = el.parentElement;
    return !parent || getComputedStyle(parent).cursor !== 'pointer';
  };

  const interactiveRole = (el, insideInteractive) => {
    const role = roleOf(el);
    if (role) return role;
    if (insideInteractive) return '';
    const tabindex = el.getAttribute('tabindex');
    if (tabindex !== null && +tabindex >= 0 && el !== document.body) return 'clickable';
    return pointerTarget(el) ? 'clickable' : '';
  };

  const nameOf = el => {
    const labelled = el.getAttribute('aria-labelledby');
    if (labelled) {
      const text = labelled.split(/\s+/).map(id => document.getElementById(id)?.innerText || '').join(' ');
      if (clean(text)) return clean(text);
    }
    const label = el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText);
    if (clean(label)) return clean(label);
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') return clean(el.placeholder || el.title || el.name);
    const text = el.innerText;
    if (clean(text)) return clean(text);
    const img = el.querySelector && el.querySelector('img[alt]');
    return clean(el.title || (img && img.alt) || el.getAttribute('alt') || '');
  };

  const shortHref = el => {
    try {
      const url = new URL(el.href, location.href);
      return clean(url.origin === location.origin ? url.pathname + url.search : url.href, 120);
    } catch (error) {
      return clean(el.getAttribute('href'), 120);
    }
  };

  const lineFor = (el, role) => {
    let line = `[${refFor(el)}] ${role}`;
    const name = nameOf(el);
    if (name) line += ` "${name}"`;
    if (role === 'textbox' || role === 'searchbox' || role === 'combobox' || role === 'slider') {
      let value = el.tagName === 'SELECT' ? (el.selectedOptions[0] || {}).text : el.value;
      if (el.isContentEditable && el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA') value = el.innerText;
      if (el.type === 'password') value = value ? '•••' : '';
      if (value) line += ` = "${clean(value, 120)}"`;
    }
    const states = [];
    if (el.checked || el.getAttribute('aria-checked') === 'true') states.push('checked');
    if (el.getAttribute('aria-pressed') === 'true') states.push('pressed');
    if (el.getAttribute('aria-selected') === 'true') states.push('selected');
    const expanded = el.getAttribute('aria-expanded');
    if (expanded) states.push(expanded === 'true' ? 'expanded' : 'collapsed');
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') states.push('disabled');
    if (el === document.activeElement) states.push('focused');
    if (states.length) line += ` (${states.join(', ')})`;
    if (role === 'link' && el.href) line += ` → ${shortHref(el)}`;
    return line;
  };

  k.snapshot = (full, limit) => {
    const lines = [];
    let text = '';
    let size = 0;
    let cut = false;
    const push = line => {
      if (size + line.length > limit) { cut = true; return; }
      lines.push(line);
      size += line.length + 1;
    };
    const flush = () => { const value = clean(text, 600); text = ''; if (value) push(value); };
    const inView = rect => full || (rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth);

    const visit = (node, insideInteractive) => {
      if (cut) return;
      if (node.nodeType === 3) {
        const parent = node.parentElement;
        if (parent && node.data.trim() && inView(parent.getBoundingClientRect())) text += ' ' + node.data;
        return;
      }
      if (node.nodeType !== 1 && node.nodeType !== 11) return;
      if (node.nodeType === 1) {
        const el = node;
        if (el === host || SKIP.has(el.tagName) || !visible(el)) return;
        const tag = el.tagName;
        if (tag === 'IFRAME' || tag === 'FRAME') {
          const rect = el.getBoundingClientRect();
          if (rect.width > 20 && rect.height > 20 && inView(rect)) {
            flush();
            push(`[${refFor(el)}] frame "${clean(el.title || el.name || el.src, 100)}" (its content is not listed; click it by ref or coordinates)`);
          }
          return;
        }
        if (/^H[1-6]$/.test(tag)) {
          if (inView(el.getBoundingClientRect())) { flush(); push('#'.repeat(+tag[1]) + ' ' + clean(el.innerText, 200)); }
          return;
        }
        if (tag === 'IMG') {
          const rect = el.getBoundingClientRect();
          if (el.alt && rect.width >= 32 && inView(rect)) { flush(); push(`image "${clean(el.alt)}"`); }
          return;
        }
        const role = interactiveRole(el, insideInteractive);
        if (role) {
          const rect = el.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0 && inView(rect)) { flush(); push(lineFor(el, role)); }
          if (LEAF.has(tag) || ROLES.has(role) || role === 'clickable' && clean(el.innerText).length < 80) return;
          insideInteractive = true;
        }
        if (BLOCK.has(tag)) flush();
        const children = tag === 'SLOT' ? el.assignedNodes({ flatten: true })
          : el.shadowRoot ? el.shadowRoot.childNodes : el.childNodes;
        for (const child of children) visit(child, insideInteractive);
        if (BLOCK.has(tag)) flush();
        return;
      }
      for (const child of node.childNodes) visit(child, insideInteractive);
    };
    if (document.body) visit(document.body, false);
    flush();
    const max = Math.max(0, document.documentElement.scrollHeight - innerHeight);
    const head = [
      `Page: ${clean(document.title, 200)}`,
      `URL: ${location.href}`,
      `Viewport ${innerWidth}×${innerHeight}, scrolled ${Math.round(scrollY)} of ${max} px` +
        (full ? ' (whole page)' : ' (what is on screen now)'),
      '',
    ];
    if (cut) lines.push(`… cut at ${limit} characters`);
    return head.concat(lines).join('\n');
  };

  k.text = limit => {
    const value = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').trim();
    return value.length > limit ? value.slice(0, limit) + `\n… cut at ${limit} of ${value.length} characters` : value;
  };

  const deepHit = (x, y) => {
    let hit = document.elementFromPoint(x, y);
    while (hit && hit.shadowRoot) {
      const inner = hit.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === hit) break;
      hit = inner;
    }
    return hit;
  };
  const within = (node, ancestor) => {
    for (let current = node; current; current = current.parentNode || current.host) {
      if (current === ancestor) return true;
    }
    return false;
  };
  const describeEl = el => {
    if (!el) return 'nothing';
    const role = roleOf(el) || el.tagName.toLowerCase();
    const name = nameOf(el);
    return name ? `${role} "${name}"` : role + (el.id ? '#' + el.id : '');
  };

  // Where to put the pointer so the click lands on this element and not on whatever covers it.
  k.target = ref => {
    if (!byRef.has(ref)) return { error: `${ref} was not listed on this page; take a snapshot and use its refs` };
    const el = byRef.get(ref).deref();
    if (!el || !el.isConnected) return { error: `${ref} is gone: the page changed since it was listed; look at it again` };
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return { error: `${ref} is hidden now` };
    const label = describeEl(el);
    const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
    if (cy < 0 || cy > innerHeight || cx < 0 || cx > innerWidth) {
      return { scroll: [cx < 0 || cx > innerWidth ? cx - innerWidth / 2 : 0, cy < 0 || cy > innerHeight ? cy - innerHeight / 2 : 0], label };
    }
    const spots = [[0.5, 0.5], [0.3, 0.5], [0.7, 0.5], [0.5, 0.3], [0.5, 0.7], [0.2, 0.2], [0.8, 0.8], [0.2, 0.8], [0.8, 0.2]];
    for (const [fx, fy] of spots) {
      const x = rect.left + rect.width * fx, y = rect.top + rect.height * fy;
      if (x < 1 || y < 1 || x > innerWidth - 1 || y > innerHeight - 1) continue;
      const hit = deepHit(x, y);
      if (hit && (within(hit, el) || within(el, hit))) return { x, y, label };
    }
    return { error: `${ref} (${label}) is covered by ${describeEl(deepHit(cx, cy))}; close or scroll that away first` };
  };

  k.at = (x, y) => describeEl(deepHit(x, y));
  k.reveal = ref => {
    const el = byRef.get(ref) && byRef.get(ref).deref();
    if (el && el.isConnected) el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
  };
  k.info = () => ({ w: innerWidth, h: innerHeight, url: location.href, title: document.title, ready: document.readyState,
    mac: /Mac|iPhone|iPad/.test(navigator.platform || '') });
  k.waitText = (needle, ms) => new Promise(resolve => {
    const until = performance.now() + ms;
    const check = () => {
      if (document.body && document.body.innerText.includes(needle)) return resolve(true);
      if (performance.now() > until) return resolve(false);
      setTimeout(check, 100);
    };
    check();
  });

  // ---------------------------------------------------------------- the overlay
  const CSS = `
:host { all: initial; }
.root { position: fixed; inset: 0; pointer-events: none; opacity: 0; transition: opacity .5s ease;
  font: 600 13px/1.2 system-ui, -apple-system, "Segoe UI", Inter, Roboto, sans-serif; }
.root.on { opacity: 1; }
.frame { position: absolute; inset: 0; padding: 3px; overflow: hidden;
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude; }
.spin { position: absolute; left: 50%; top: 50%; width: 260vmax; height: 260vmax; margin: -130vmax 0 0 -130vmax;
  background: conic-gradient(from 0deg, rgba(56,189,248,0) 0%, #38bdf8 10%, #93c5fd 17%, rgba(59,130,246,0) 30%,
    rgba(56,189,248,0) 50%, #22d3ee 60%, #3b82f6 68%, rgba(56,189,248,0) 80%);
  animation: spin 6.5s linear infinite; }
.glow { position: absolute; inset: 0; animation: breathe 3.4s ease-in-out infinite;
  box-shadow: inset 0 0 0 1px rgba(125,211,252,.5), inset 0 0 26px 5px rgba(59,130,246,.42),
    inset 0 0 90px 14px rgba(56,189,248,.16); }
@keyframes spin { to { transform: rotate(1turn); } }
@keyframes breathe { 0%, 100% { opacity: .5; } 50% { opacity: 1; } }

.pill { position: absolute; top: 14px; left: 50%; display: flex; align-items: center; gap: 10px;
  padding: 6px 6px 6px 12px; border-radius: 999px; overflow: hidden; pointer-events: auto; color: #f0f9ff;
  background: linear-gradient(120deg, #1d4ed8 0%, #2563eb 30%, #0ea5e9 70%, #22d3ee 100%);
  box-shadow: 0 10px 30px -8px rgba(14,165,233,.75), 0 0 0 1px rgba(186,230,253,.5) inset;
  opacity: 0; transform: translate(-50%, -14px) scale(.96);
  transition: transform .55s cubic-bezier(.2,.9,.25,1.25), opacity .35s ease; }
.root.on .pill { opacity: 1; transform: translate(-50%, 0) scale(1); }
.pill.busy { pointer-events: none; }
.pill::before { content: ""; position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(105deg, transparent 38%, rgba(255,255,255,.38) 50%, transparent 62%);
  transform: translateX(-130%); animation: sheen 4.2s ease-in-out infinite; }
@keyframes sheen { 0%, 60% { transform: translateX(-130%); } 100% { transform: translateX(130%); } }
.mark { width: 18px; height: 18px; flex: none; filter: drop-shadow(0 0 4px rgba(255,255,255,.55)); }
.mark .caret { animation: blink 1.05s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.label { position: relative; white-space: nowrap; letter-spacing: .01em; max-width: 46vw; overflow: hidden;
  text-overflow: ellipsis; transition: opacity .2s ease, transform .2s ease; text-shadow: 0 1px 2px rgba(2,6,23,.35); }
.label.swap { opacity: 0; transform: translateY(4px); }
.stop { all: unset; position: relative; display: flex; align-items: center; gap: 6px; padding: 6px 12px;
  border-radius: 999px; background: rgba(7,18,42,.82); color: #e0f2fe; cursor: pointer;
  font: 600 12px/1 system-ui, -apple-system, "Segoe UI", sans-serif; transition: background .2s ease, transform .15s ease; }
.stop:hover { background: rgba(7,18,42,.96); }
.stop:active { transform: scale(.95); }
.stop i { width: 8px; height: 8px; border-radius: 2px; background: currentColor; }
.root.stopped .frame, .root.stopped .glow { opacity: 0; transition: opacity .7s ease; }
.root.stopped .stop { display: none; }

.cursor { position: absolute; left: 0; top: 0; width: 26px; height: 30px; opacity: 0; will-change: transform;
  transition: opacity .35s ease;
  filter: drop-shadow(0 0 7px rgba(56,189,248,.9)) drop-shadow(0 2px 3px rgba(2,6,23,.45)); }
.cursor.shown { opacity: 1; }
.cursor svg { display: block; transform-origin: 2px 2px; transition: transform .12s ease; }
.cursor.down svg { transform: scale(.84); }
.tag { position: absolute; left: 19px; top: 23px; padding: 2px 8px; border-radius: 8px 8px 8px 3px; color: #fff;
  background: linear-gradient(135deg, #2563eb, #0ea5e9); white-space: nowrap;
  font: 700 11px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; box-shadow: 0 4px 14px -3px rgba(14,165,233,.7); }
.ripple { position: absolute; width: 14px; height: 14px; margin: -7px 0 0 -7px; border-radius: 50%;
  border: 2px solid #7dd3fc; box-shadow: 0 0 14px #38bdf8, inset 0 0 6px #38bdf8; animation: ripple .65s ease-out forwards; }
@keyframes ripple { from { transform: scale(.35); opacity: 1; } to { transform: scale(4.4); opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .spin, .glow, .pill::before, .mark .caret { animation: none; } }
`;
  const SVG = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs, parent) => {
    const node = document.createElementNS(SVG, tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
    if (parent) parent.appendChild(node);
    return node;
  };
  const div = (cls, parent) => {
    const node = document.createElement('div');
    node.className = cls;
    if (parent) parent.appendChild(node);
    return node;
  };
  const say = (en, ru) => (RU ? ru : en);
  const IDLE = () => say('Kvist is working here', 'Kvist работает здесь');

  let ui = null;
  let observer = null;
  let idleTimer = 0;
  let busyTimer = 0;
  let glideId = 0;

  const build = () => {
    host = document.createElement('div');
    // Plain tag, no id or class: nothing for page styles to match.
    const root = host.attachShadow({ mode: 'closed' });
    if (root.adoptedStyleSheets !== undefined && typeof CSSStyleSheet === 'function') {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(CSS);
      root.adoptedStyleSheets = [sheet];
    } else {
      const style = document.createElement('style');
      style.textContent = CSS;
      root.appendChild(style);
    }
    const box = div('root', root);
    div('spin', div('frame', box));
    div('glow', box);
    const pill = div('pill', box);
    const mark = svg('svg', { class: 'mark', viewBox: '0 0 512 512' }, pill);
    svg('path', { d: 'M150 150 L266 256 L150 362', fill: 'none', stroke: '#fff', 'stroke-width': '52',
      'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, mark);
    svg('rect', { class: 'caret', x: '286', y: '330', width: '130', height: '48', rx: '24', fill: '#fff' }, mark);
    const label = div('label', pill);
    label.textContent = IDLE();
    const stop = document.createElement('button');
    stop.className = 'stop';
    stop.type = 'button';
    stop.appendChild(document.createElement('i'));
    stop.appendChild(document.createTextNode(say('Stop', 'Стоп')));
    pill.appendChild(stop);
    stop.addEventListener('click', event => {
      if (!event.isTrusted) return;  // only the person's own click stops Kvist
      event.preventDefault();
      event.stopPropagation();
      k.stopped = true;
      setLabel(say('Stopped', 'Остановлено'));
      box.classList.add('stopped');
      setTimeout(() => k.unmount(), 1600);
    }, true);
    const cursor = div('cursor', box);
    const arrow = svg('svg', { width: '26', height: '30', viewBox: '0 0 26 30' }, cursor);
    const defs = svg('defs', {}, arrow);
    const gradient = svg('linearGradient', { id: 'kvist-cursor-fill', x1: '0', y1: '0', x2: '0.6', y2: '1' }, defs);
    svg('stop', { offset: '0', 'stop-color': '#7dd3fc' }, gradient);
    svg('stop', { offset: '1', 'stop-color': '#2563eb' }, gradient);
    svg('path', { d: 'M2 2 L2 22.5 L7.4 17.4 L11.2 25.8 L15 24.1 L11.3 15.9 L18.6 15.9 Z', fill: 'url(#kvist-cursor-fill)',
      stroke: '#fff', 'stroke-width': '1.6', 'stroke-linejoin': 'round' }, arrow);
    const tag = div('tag', cursor);
    tag.textContent = 'Kvist';
    ui = { box, pill, label, cursor };
  };

  const place = () => {
    if (!document.documentElement || host.isConnected) return;
    document.documentElement.appendChild(host);
    // The top layer keeps the overlay above the page's own modal dialogs.
    host.setAttribute('popover', 'manual');
    try { host.showPopover(); } catch (error) { /* older browsers: z-index below still works */ }
  };

  const setLabel = text => {
    if (!ui || ui.label.textContent === text) return;
    ui.label.classList.add('swap');
    setTimeout(() => { ui.label.textContent = text; ui.label.classList.remove('swap'); }, 180);
  };

  const moveCursor = (x, y) => {
    k.cx = x; k.cy = y;
    ui.cursor.style.transform = `translate(${x - 2}px, ${y - 2}px)`;
    ui.cursor.classList.add('shown');
  };

  k.mount = () => {
    if (k.stopped) return false;
    if (!host) build();
    if (!document.documentElement) {
      document.addEventListener('readystatechange', () => k.mount(), { once: true });
      return true;
    }
    host.style.cssText = 'all: initial !important; position: fixed !important; inset: 0 !important; display: block !important;' +
      'width: 100vw !important; height: 100vh !important; margin: 0 !important; padding: 0 !important; border: 0 !important;' +
      'background: transparent !important; overflow: visible !important; pointer-events: none !important;' +
      'z-index: 2147483647 !important;';
    place();
    if (!observer) {
      // Single-page apps rebuild the document; put the overlay back when it is removed.
      observer = new MutationObserver(() => { if (!host.isConnected) place(); });
      observer.observe(document.documentElement, { childList: true });
    }
    requestAnimationFrame(() => ui.box.classList.add('on'));
    if (k.cx < 0) moveCursor(innerWidth / 2, innerHeight / 2);
    return true;
  };

  k.unmount = () => {
    if (observer) { observer.disconnect(); observer = null; }
    if (host && host.isConnected) {
      ui.box.classList.remove('on');
      const gone = host;
      setTimeout(() => gone.remove(), 500);
    }
  };

  k.status = (en, ru) => {
    if (!ui) return;
    setLabel(say(en, ru));
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => setLabel(IDLE()), 2600);
  };

  // The pill must not catch a click the agent aims at something under it.
  k.busy = () => {
    if (!ui) return;
    ui.pill.classList.add('busy');
    clearTimeout(busyTimer);
    busyTimer = setTimeout(() => ui.pill.classList.remove('busy'), 900);
  };

  k.glide = (points, stepMs) => {
    if (!ui || !points.length) return;
    k.busy();
    const id = ++glideId;
    const start = performance.now();
    const frame = now => {
      if (id !== glideId) return;
      const index = Math.min(points.length - 1, Math.floor((now - start) / stepMs));
      moveCursor(points[index][0], points[index][1]);
      if (index < points.length - 1) requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  };

  k.point = (x, y, down) => {
    if (!ui) return;
    k.busy();
    glideId += 1;
    moveCursor(x, y);
    ui.cursor.classList.toggle('down', down);
    if (!down) return;
    const ring = div('ripple', ui.box);
    ring.style.left = x + 'px';
    ring.style.top = y + 'px';
    setTimeout(() => ring.remove(), 700);
  };

  // The host's inline style is all !important, so this has to be too.
  k.hide = hidden => { if (host) host.style.setProperty('visibility', hidden ? 'hidden' : 'visible', 'important'); };
})();
