# Page format

Pass an object as `page` to `visualize__validate` or `visualize__show`.
Unknown fields are errors. Text is plain text (HTML and Markdown are not
executed). No external images, scripts, model URLs or custom CSS.

Page: `title` (required, 160 chars), `summary` (4000 chars), `language`
(`en` or `ru`, default en), `theme` (`dark` or `light`, default dark),
`demo` (boolean, default false), `sections` (1–20 objects). Max total: 500 KB.

Every section has `type`, `title` (required, 100 chars), optional `description`
(4000 chars) and `source` (500 chars, displayed as text). Choose one schema:

- `text`: `body` (required, plain text, 4000 chars).
- `metrics`: `items` (1–8): `{label, value, note?}`. `value` is a formatted
  string, not an inferred total. Label/value ≤100 chars, note ≤300.
- `diagram`: `nodes` (1–120), `edges` (0–240, required).
  Node: `{id, label, column, row, detail?, color?}`. Unique `id` ≤64 chars;
  label ≤100, detail ≤4000. `column` and `row` are integers 0–40 on a grid;
  two nodes cannot occupy the same cell. Columns normally run left to right.
  Edge: `{from, to, label?}` with existing node IDs, label ≤80 chars.
- `chart`: `kind` (`line`, `bar` or `donut`), `labels` (1–100 strings ≤100),
  `series` (1–6): `{name, values}`; name ≤100, exactly one finite number per
  label, between −1e12 and 1e12. Optional `unit` ≤40 chars. A donut has one
  series, at most six categories, no negative values and a positive total.
  Mixed units need separate chart sections. Zero and negative line/bar
  values are supported. Do not send NaN, Infinity or numeric strings.
- `table`: `columns` (1–20 strings ≤100), `rows` (1–500 arrays, exactly one
  cell per column). Cells: string ≤2000, finite number within ±1e12, boolean
  or null. CSV preserves numeric values and neutralizes formula-like strings.
- `scene3d`: `objects` (1–60), optional `edges` (0–120, same as diagram).
  Object: `{id, label, shape, position, size?, color?, detail?}`. Unique IDs;
  `shape`: `box`, `sphere`, `cone`; `position`: `[x,y,z]`, finite coordinates
  in −100…100, Y points up; `size`: 0.1…20 (default 1, approximate diameter).
  Frame fits objects automatically. Avoid intersecting objects: the small
  software renderer sorts faces by depth and is not a CAD intersection engine.
  Edge labels and full object details remain in the textual alternative.

Colours: `blue`, `green`, `orange`, `purple`, `cyan`, `pink`. They adapt to the
theme. Text, tables, diagrams and numeric charts are rendered before JavaScript;
3D and interaction use the page's bundled script. No network is required.

## Architecture example

```json
{
  "title": "How a request reaches the database",
  "summary": "The API validates the request before accessing stored data.",
  "language": "en",
  "sections": [{
    "type": "diagram", "title": "Request flow",
    "nodes": [
      {"id": "client", "label": "Client", "column": 0, "row": 0, "detail": "Sends a request."},
      {"id": "api", "label": "API", "column": 1, "row": 0, "detail": "Validates input and checks access."},
      {"id": "db", "label": "Database", "column": 2, "row": 0, "detail": "Stores application records."}
    ],
    "edges": [{"from": "client", "to": "api", "label": "HTTPS"}, {"from": "api", "to": "db", "label": "Query"}]
  }]
}
```

## A 3D section

```json
{
  "type": "scene3d", "title": "Spatial layout",
  "description": "Conceptual placement; not a physical scale model.",
  "objects": [
    {"id": "a", "label": "Gateway", "shape": "sphere", "position": [-3, 0, 0], "color": "blue"},
    {"id": "b", "label": "Worker", "shape": "box", "position": [0, 2, 1], "color": "green"},
    {"id": "c", "label": "Storage", "shape": "cone", "position": [3, 0, -1], "color": "orange", "size": 1.5}
  ],
  "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
}
```

`show` defaults to `open_browser: true`. Invalid input returns
`{valid:false, errors:[...], opened:false}` without writing or opening.
Success returns `{valid:true, artifact:{id,path,...}, opened:true}`. If the
browser could not launch, `opened:false` and `open_error` explain why;
the artifact remains available. Use `open_browser:false` for file-only delivery.
