'use strict';
const root = document.querySelector('#states');
let group = null, list = null;
for (const [section, name, query] of window.PROTO_STATES) {
  if (section !== group) {
    group = section;
    const h = document.createElement('h2'); h.textContent = section; list = document.createElement('ul');
    root.append(h, list);
  }
  const li = document.createElement('li'), a = document.createElement('a');
  a.href = `app.html?${query}`; a.textContent = name; li.append(a); list.append(li);
}
