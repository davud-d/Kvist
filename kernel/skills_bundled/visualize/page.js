'use strict';
const page = JSON.parse(document.getElementById('page-data').textContent);
const ru = page.language === 'ru';
const originalHTML = '<!doctype html>\n' + document.documentElement.outerHTML;
const words = (en, other) => ru ? other : en;
function download(content, type, name) {
  const url = URL.createObjectURL(new Blob([content], {type}));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
document.getElementById('download').addEventListener('click', () => download(originalHTML, 'text/html;charset=utf-8', 'visualization.html'));
document.getElementById('theme').addEventListener('click', () => {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
  document.dispatchEvent(new Event('visual-theme'));
});
function csvCell(value) {
  let text = value === null ? '' : String(value);
  // Spreadsheet programs can evaluate strings even when CSV quotes surround them.
  if (typeof value === 'string' && /^[\s]*[=+@-]|^[\t\r\n]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}
function csvRows(section) {
  if (section.type === 'table') return [section.columns, ...section.rows];
  return [[words('Category', 'Категория'), ...section.series.map(s => s.name)],
    ...section.labels.map((label, i) => [label, ...section.series.map(s => s.values[i])])];
}
document.querySelectorAll('[data-csv]').forEach(button => button.addEventListener('click', () => {
  const section = page.sections[Number(button.closest('section').dataset.index)];
  download('\ufeff' + csvRows(section).map(row => row.map(csvCell).join(',')).join('\r\n'), 'text/csv;charset=utf-8', 'data.csv');
}));
document.querySelectorAll('[data-fullscreen]').forEach(button => {
  if (!document.fullscreenEnabled) { button.hidden = true; return; }
  button.addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await button.closest('.visual').requestFullscreen();
    } catch (error) {
      const status = button.closest('.visual').querySelector('.selection,.scene-status');
      status.textContent = words('Fullscreen is unavailable: ', 'Полный экран недоступен: ') + error.message;
    }
  });
});
document.querySelectorAll('.diagram').forEach(panel => {
  const section = page.sections[Number(panel.closest('section').dataset.index)];
  const svg = panel.querySelector('svg'), viewport = panel.querySelector('.viewport');
  const status = panel.querySelector('.selection');
  const initialStatus = status.textContent;
  let zoom = 1;
  function scale(factor) { zoom = Math.max(.25, Math.min(4, zoom * factor)); svg.style.width = `${Number(svg.dataset.width)*zoom}px`; svg.style.minWidth = '0'; }
  panel.querySelectorAll('[data-zoom]').forEach(b => b.addEventListener('click', () => scale(b.dataset.zoom === 'in' ? 1.25 : .8)));
  const nodes = Array.from(panel.querySelectorAll('[data-node]'));
  function select(node) {
    nodes.forEach(n => n.classList.toggle('selected', n === node));
    const data = section.nodes[Number(node.dataset.node)];
    status.textContent = data.label + (data.detail ? '\n' + data.detail : '');
  }
  nodes.forEach(node => {
    node.addEventListener('click', () => select(node));
    node.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(node); }
    });
  });
  panel.querySelector('[data-find]').addEventListener('input', event => {
    const query = event.target.value.trim().toLocaleLowerCase();
    const matches = nodes.filter(node => {
      const data = section.nodes[Number(node.dataset.node)];
      const match = query && `${data.label} ${data.detail || ''}`.toLocaleLowerCase().includes(query);
      node.classList.toggle('match', Boolean(match)); return match;
    });
    status.textContent = query ? `${words('Matches', 'Найдено')}: ${matches.length}` : initialStatus;
    if (matches.length) {
      const box = matches[0].getBoundingClientRect(), parent = viewport.getBoundingClientRect();
      viewport.scrollLeft += box.left - parent.left - 40;
      viewport.scrollTop += box.top - parent.top - 40;
    }
  });
  panel.querySelector('[data-reset]').addEventListener('click', () => {
    zoom = 1; scale(1); viewport.scrollTo(0, 0); nodes.forEach(n => n.classList.remove('selected', 'match'));
    panel.querySelector('[data-find]').value = ''; status.textContent = initialStatus;
  });
});
