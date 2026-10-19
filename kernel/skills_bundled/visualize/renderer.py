# -*- coding: utf-8 -*-
"""Render a bounded declarative page; model content is always text, never code."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import textwrap
from pathlib import Path

from kernel.plugins.schema import validate

ROOT = Path(__file__).parent
PALETTE = ("blue", "green", "orange", "purple", "cyan", "pink")


def obj(properties, required=()):
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(required)}


def arr(items, maximum=200, minimum=1):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


def string(maximum=200, minimum=0):
    return {"type": "string", "maxLength": maximum, "minLength": minimum}


TEXT = string(4000)
LABEL = string(100, 1)
NUMBER = {"type": "number", "minimum": -1e12, "maximum": 1e12}
COORD = {"type": "integer", "minimum": 0, "maximum": 40}
VEC = {"type": "array", "items": {"type": "number", "minimum": -100, "maximum": 100}, "minItems": 3, "maxItems": 3}
CELL = {"type": ["string", "number", "boolean", "null"], "maxLength": 2000, "minimum": -1e12, "maximum": 1e12}
NODE = obj({"id": string(64, 1), "label": LABEL, "detail": TEXT, "column": COORD, "row": COORD,
            "color": {"enum": list(PALETTE)}}, ("id", "label", "column", "row"))
EDGE = obj({"from": string(64, 1), "to": string(64, 1), "label": string(80)}, ("from", "to"))
OBJECT = obj({"id": string(64, 1), "label": LABEL, "detail": TEXT, "position": VEC,
              "shape": {"enum": ["box", "sphere", "cone"]},
              "size": {"type": "number", "minimum": 0.1, "maximum": 20},
              "color": {"enum": list(PALETTE)}}, ("id", "label", "position", "shape"))
BASE = {"type": string(20), "title": LABEL, "description": TEXT, "source": string(500)}
SECTIONS = {
    "text": obj({**BASE, "body": TEXT}, ("type", "title", "body")),
    "metrics": obj({**BASE, "items": arr(obj({"label": LABEL, "value": string(100, 1), "note": string(300)},
                                            ("label", "value")), 8)}, ("type", "title", "items")),
    "diagram": obj({**BASE, "nodes": arr(NODE, 120), "edges": arr(EDGE, 240, 0)},
                   ("type", "title", "nodes", "edges")),
    "chart": obj({**BASE, "kind": {"enum": ["line", "bar", "donut"]}, "unit": string(40),
                  "labels": arr(LABEL, 100),
                  "series": arr(obj({"name": LABEL, "values": arr(NUMBER, 100)}, ("name", "values")), 6)},
                 ("type", "title", "kind", "labels", "series")),
    "table": obj({**BASE, "columns": arr(LABEL, 20), "rows": arr(arr(CELL, 20), 500)},
                 ("type", "title", "columns", "rows")),
    "scene3d": obj({**BASE, "objects": arr(OBJECT, 60), "edges": arr(EDGE, 120, 0)},
                   ("type", "title", "objects")),
}
PAGE = obj({"title": string(160, 1), "summary": TEXT, "language": {"enum": ["en", "ru"]},
            "theme": {"enum": ["light", "dark"]}, "demo": {"type": "boolean"},
            "sections": arr({"type": "object"}, 20)}, ("title", "sections"))


def validate_page(page):
    try:
        encoded = json.dumps(page, ensure_ascii=False, allow_nan=False)
        size = len(encoded.encode("utf-8"))
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return ["page: use JSON values and finite numbers only"]
    if size > 500_000:
        return ["page: description exceeds 500 KB; split it into focused pages"]
    errors = validate(page, PAGE)
    if errors:
        return errors
    for index, section in enumerate(page["sections"]):
        prefix = f"sections[{index}]"
        kind = section.get("type")
        if not isinstance(kind, str) or kind not in SECTIONS:
            errors.append(f"{prefix}.type: choose {', '.join(SECTIONS)}")
            continue
        issues = validate(section, SECTIONS[kind])
        if not issues:
            issues = semantic_errors(section)
        errors.extend(f"{prefix}{issue.removeprefix('$') }" for issue in issues)
    return errors[:30]


def semantic_errors(section):
    kind = section["type"]
    if kind in ("diagram", "scene3d"):
        nodes = section.get("nodes", section.get("objects"))
        ids = [node["id"] for node in nodes]
        errors = [".nodes: duplicate id"] if len(set(ids)) != len(ids) else []
        errors.extend(f".edges[{i}]: unknown endpoint" for i, edge in enumerate(section.get("edges", []))
                      if edge["from"] not in ids or edge["to"] not in ids)
        if kind == "diagram":
            places = [(node["column"], node["row"]) for node in nodes]
            if len(set(places)) != len(places):
                errors.append(".nodes: two nodes occupy the same column/row")
        return errors
    if kind == "table":
        return [f".rows[{i}]: expected {len(section['columns'])} cells" for i, row in enumerate(section["rows"])
                if len(row) != len(section["columns"])]
    if kind != "chart":
        return []
    errors = [f".series[{i}].values: one value is required for each label" for i, series in enumerate(section["series"])
              if len(series["values"]) != len(section["labels"])]
    if section["kind"] == "donut":
        values = section["series"][0]["values"]
        if len(section["series"]) != 1 or len(values) > 6 or min(values) < 0 or sum(values) <= 0:
            errors.append(".series: donut needs one series with at most six non-negative values and a positive total")
    return errors


def esc(value):
    return html.escape(str(value), quote=True)


def tr(page, en, ru):
    return ru if page.get("language") == "ru" else en


def json_data(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("&", "\\u0026")


def table(columns, rows):
    head = "".join(f'<th scope="col">{esc(value)}</th>' for value in columns)
    body = "".join('<tr>' + ''.join(f'<td>{esc("" if value is None else value)}</td>' for value in row) + '</tr>'
                   for row in rows)
    return f'<div class="table-scroll" tabindex="0"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def data_table(columns, rows, page):
    return (f'<details class="data"><summary>{tr(page, "Data and CSV", "Данные и CSV")}</summary>'
            f'<button data-csv>{tr(page, "Download CSV", "Скачать CSV")}</button>{table(columns, rows)}</details>')


def svg_text(x, y, value, attrs=""):
    return f'<text x="{x:g}" y="{y:g}" {attrs}>{esc(value)}</text>'


def svg_wrap(content, width, height, title, css=""):
    size = f' style="width:{width}px;max-width:none" data-width="{width}"' if css == 'diagram-svg' else ''
    return (f'<svg class="{css}"{size} xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="{esc(title)}"><title>{esc(title)}</title>{content}</svg>')


def graph_controls(page, scene=False):
    search = '' if scene else f'<label>{tr(page, "Find", "Найти")} <input type="search" data-find></label>'
    rotate = '' if not scene else ''.join(f'<button data-turn="{direction}" aria-label="{label}">{symbol}</button>' for direction, label, symbol in (
        ('left', tr(page, 'Rotate left', 'Повернуть влево'), '←'), ('right', tr(page, 'Rotate right', 'Повернуть вправо'), '→'),
        ('up', tr(page, 'Rotate up', 'Повернуть вверх'), '↑'), ('down', tr(page, 'Rotate down', 'Повернуть вниз'), '↓')))
    return (f'<div class="controls">{search}{rotate}<button data-zoom="in" aria-label="{tr(page,"Zoom in","Приблизить")}">+</button>'
            f'<button data-zoom="out" aria-label="{tr(page,"Zoom out","Отдалить")}">−</button>'
            f'<button data-reset>{tr(page,"Reset view","Сбросить вид")}</button>'
            f'<button data-fullscreen>{tr(page,"Fullscreen","На весь экран")}</button></div>')


def graph_alternative(nodes, edges, page):
    names = {node['id']: node['label'] for node in nodes}
    items = ''.join(f'<dt>{esc(node["label"])}</dt><dd>{esc(node.get("detail", ""))}</dd>' for node in nodes)
    links = ''.join(f'<li>{esc(names[e["from"]])} → {esc(names[e["to"]])}: {esc(e.get("label", ""))}</li>' for e in edges)
    return f'<details><summary>{tr(page,"Components and connections","Компоненты и связи")}</summary><dl>{items}</dl><ul>{links}</ul></details>'


def diagram(section, page):
    nodes, edges = section['nodes'], section['edges']
    positions = {node['id']: (80 + node['column'] * 330, 80 + node['row'] * 190) for node in nodes}
    width = max(x for x, _ in positions.values()) + 330
    height = max(y for _, y in positions.values()) + 200
    parts = ['<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/></marker></defs>']
    for edge in edges:
        x, y = positions[edge['from']]
        tx, ty = positions[edge['to']]
        start, end = x + 240, tx
        control = max(45, abs(end - start) / 2)
        path = f'M {start} {y+48} C {start+control} {y+48}, {end-control} {ty+48}, {end} {ty+48}'
        if end <= start:
            path = f'M {x+120} {y} C {x+120} {y-55}, {tx+120} {ty-55}, {tx+120} {ty}'
        if edge['from'] == edge['to']:
            path = f'M {x+90} {y} C {x+50} {y-65}, {x+190} {y-65}, {x+150} {y}'
        parts.append(f'<path class="edge" d="{path}" marker-end="url(#arrow)"/>')
        parts.append(svg_text((start+end)/2, (y+ty)/2+32, edge.get('label', ''), 'class="edge-label" text-anchor="middle"'))
    for i, node in enumerate(nodes):
        x, y = positions[node['id']]
        color = node.get('color', PALETTE[i % len(PALETTE)])
        lines = textwrap.wrap(node['label'], 20)
        shown = lines[:4]
        if len(lines) > 4:
            shown[-1] = shown[-1][:19] + '…'
        label = ''.join(svg_text(x+16, y+27+j*23, line) for j, line in enumerate(shown))
        parts.append(f'<g class="node {color}" data-node="{i}" tabindex="0" role="button" aria-label="{esc(node["label"])}">'
                     f'<rect x="{x}" y="{y}" width="240" height="132" rx="12"/>{label}</g>')
    return (f'<div class="visual diagram">{graph_controls(page)}<div class="viewport" tabindex="0">'
            f'{svg_wrap("".join(parts), width, height, section["title"], "diagram-svg")}</div>'
            f'<p class="selection" aria-live="polite">{tr(page,"Select a component to read its details.","Выберите компонент, чтобы прочитать подробности.")}</p></div>'
            + graph_alternative(nodes, edges, page))


def chart(section, page):
    rows = [[label] + [series['values'][i] for series in section['series']] for i, label in enumerate(section['labels'])]
    columns = [tr(page, 'Category', 'Категория')] + [s['name'] for s in section['series']]
    plot = donut(section) if section['kind'] == 'donut' else xy_chart(section)
    legend_names = section['labels'] if section['kind'] == 'donut' else [s['name'] for s in section['series']]
    legend = ''.join(f'<span class="legend-item {PALETTE[i % 6]}"><i></i>{esc(name)}</span>' for i, name in enumerate(legend_names))
    return f'<div class="chart-scroll">{plot}</div><div class="legend">{legend}</div>{data_table(columns, rows, page)}'


def xy_chart(section):
    labels, series = section['labels'], section['series']
    values = [v for s in series for v in s['values']]
    low, high = min(0, min(values)), max(0, max(values))
    span = high - low or 1
    high += span * .1
    width = max(1000, len(labels) * (80 if section['kind'] == 'bar' else 50))
    left, right, top, bottom = 100, width - 40, 40, 400
    y = lambda value: bottom - (value - low) / (high - low) * (bottom - top)
    step = (right - left) / max(1, len(labels))
    parts = [svg_text(left, 22, section.get('unit', ''), 'class="axis"')]
    for i in range(6):
        value = low + (high-low) * i/5
        yy = y(value)
        parts.append(f'<path class="grid" d="M {left} {yy} H {right}"/>')
        parts.append(svg_text(left-12, yy+5, f'{value:.3g}', 'class="axis" text-anchor="end"'))
    for i, label in enumerate(labels):
        xx = left + step * (i + .5)
        short = label if len(label) <= 18 else label[:17] + '…'
        parts.append(svg_text(xx, bottom+36, short, 'class="axis" text-anchor="end" transform="rotate(-25 '+f'{xx} {bottom+36}'+')"'))
    for si, s in enumerate(series):
        color = PALETTE[si]
        points = [(left + step * (i+.5), y(v)) for i, v in enumerate(s['values'])]
        if section['kind'] == 'line':
            coords = ' '.join(f'{x:g},{yy:g}' for x, yy in points)
            parts.append(f'<polyline class="plot-line {color}" points="{coords}"/>')
        for i, ((xx, yy), value) in enumerate(zip(points, s['values'])):
            title = esc(f'{labels[i]} · {s["name"]}: {value} {section.get("unit", "")}')
            if section['kind'] == 'bar':
                bar_width = step * .75/len(series)
                xx += (si-len(series)/2)*bar_width
                shape = f'<rect x="{xx}" y="{min(y(0), yy)}" width="{bar_width*.9}" height="{max(.5, abs(yy-y(0)))}" rx="3"'
            else:
                shape = f'<circle cx="{xx}" cy="{yy}" r="5"'
            parts.append(f'{shape} class="mark {color}"><title>{title}</title></'+('rect>' if section['kind']=='bar' else 'circle>'))
    return svg_wrap(''.join(parts), width, 520, section['title'], 'chart-svg')


def donut(section):
    values = section['series'][0]['values']
    total, offset = sum(values), 0
    parts = []
    for i, value in enumerate(values):
        length = value / total * 100
        parts.append(f'<circle class="ring {PALETTE[i]}" cx="300" cy="250" r="165" pathLength="100" '
                     f'stroke-dasharray="{length} {100-length}" stroke-dashoffset="{-offset}" transform="rotate(-90 300 250)">'
                     f'<title>{esc(section["labels"][i])}: {value} ({length:.1f}%)</title></circle>')
        name = section['labels'][i]
        short = name if len(name) <= 26 else name[:25] + '…'
        parts.append(svg_text(570, 110+i*55, f'{short}: {length:.1f}%', 'class="donut-label"'))
        offset += length
    parts.append(svg_text(300, 260, f'{total:g}', 'class="donut-total" text-anchor="middle"'))
    return svg_wrap(''.join(parts), 1000, 500, section['title'], 'chart-svg')


def scene(section, page):
    hint = tr(page, 'Drag to orbit · Arrow keys to rotate · + / − to zoom · Home to reset',
              'Перетаскивайте для вращения · Стрелки — поворот · + / − — масштаб · Home — сброс')
    return (f'<div class="visual scene">{graph_controls(page, True)}<canvas tabindex="0" aria-label="{esc(section["title"])}">'
            f'{esc(hint)}</canvas><p class="scene-status" role="status">{esc(hint)}</p></div>'
            + graph_alternative(section['objects'], section.get('edges', []), page))


def section_html(section, page, index):
    kind = section['type']
    if kind == 'text':
        body = f'<div class="prose">{esc(section["body"])}</div>'
    elif kind == 'metrics':
        body = '<dl class="metrics">' + ''.join(f'<div><dt>{esc(item["label"])}</dt><dd>{esc(item["value"])}</dd><p>{esc(item.get("note", ""))}</p></div>' for item in section['items']) + '</dl>'
    elif kind == 'table':
        body = f'<button data-csv>{tr(page,"Download CSV","Скачать CSV")}</button>{table(section["columns"], section["rows"])}'
    else:
        body = {'diagram': diagram, 'chart': chart, 'scene3d': scene}[kind](section, page)
    if kind == 'diagram':
        body = body.replace('id="arrow"', f'id="arrow-{index}"').replace('url(#arrow)', f'url(#arrow-{index})')
    source = f'<p class="source">{tr(page,"Source","Источник")}: {esc(section["source"])}</p>' if section.get('source') else ''
    return (f'<section id="section-{index}" data-index="{index}"><div class="section-heading"><h2>{esc(section["title"])}</h2></div>'
            f'<p class="description">{esc(section.get("description", ""))}</p>{body}{source}</section>')


def render_page(page):
    errors = validate_page(page)
    if errors:
        raise ValueError('; '.join(errors))
    script = (ROOT / 'page.js').read_text(encoding='utf-8') + '\n' + (ROOT / 'scene.js').read_text(encoding='utf-8')
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    css = (ROOT / 'page.css').read_text(encoding='utf-8')
    sections = ''.join(section_html(s, page, i) for i, s in enumerate(page['sections']))
    nav = ''.join(f'<a href="#section-{i}">{esc(s["title"])}</a>' for i, s in enumerate(page['sections']))
    badge = f'<span class="demo">{tr(page,"Illustrative data","Демонстрационные данные")}</span>' if page.get('demo') else ''
    return (f'<!doctype html><html lang="{page.get("language", "en")}" data-theme="{page.get("theme", "dark")}"><head>'
            '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'sha256-{digest}\'; style-src \'unsafe-inline\'; img-src data: blob:; connect-src \'none\'; base-uri \'none\'; form-action \'none\'">'
            f'<title>{esc(page["title"])}</title><style>{css}</style></head><body>'
            f'<a class="skip" href="#main">{tr(page,"Skip to content","К содержимому")}</a>'
            '<header><a class="brand" href="#main">Kvist <span>/ visual</span></a><div class="controls">'
            f'<button id="theme">{tr(page,"Light / dark","Светлая / тёмная")}</button>'
            f'<button id="download">{tr(page,"Save page","Сохранить страницу")}</button></div></header>'
            f'<main id="main"><div class="intro">{badge}<h1>{esc(page["title"])}</h1><p>{esc(page.get("summary", ""))}</p></div>'
            f'<nav aria-label="{tr(page,"Contents","Содержание")}">{nav}</nav>{sections}</main>'
            f'<footer>Kvist · {tr(page,"An explanation you can explore","Объяснение, которое можно исследовать")}</footer>'
            f'<script type="application/json" id="page-data">{json_data(page)}</script><script>{script}</script></body></html>')
