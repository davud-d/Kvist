# Visual explanations

Turn an explanation into a page the person can explore in their own browser.
Use this when a large architecture diagram, data chart or spatial 3D scene
communicates the answer better than prose, or when the person requests one.
This is a Kvist tool skill, not a second model: YOU supply the content and data;
the renderer supplies layout, interaction, validation and browser opening.

Read `references/format.md` before the first call for the exact page schema
and examples. Read `references/design.md` when choosing the visual story.

1. Inspect the actual source material. Identify the main question, important
   components, directed relationships and evidence. Do not invent statistics
   or pass illustrative examples off as measured results. Set `demo: true`
   for sample data, and state assumptions in each section's description.
2. Compose a `page` object. Lead with the explanation, then a dominant diagram
   or chart, then supporting detail. Use the person's language. Give diagrams
   generous grid spacing; use multiple focused sections for a dense system.
   Use 3D for spatial relationships or at the person's request; ordinary
   quantitative comparisons remain easier to read in 2D.
3. Call `visualize__show` with the complete page. It validates before saving
   or opening. For iteration without side effects use `visualize__validate`.
   Fix returned field errors and retry; do not repeatedly open successful pages.
4. Report the artifact path and whether `opened` is true. If opening failed,
   the file is still saved: give its path and explain `open_error`. An accepted
   browser launch does not prove the browser painted the page.

The result is a self-contained HTML file with no network requests or server.
It remains usable after the task ends. Opening defaults to true and uses the
system default browser on Linux, macOS and Windows. A remote/headless machine
may have no graphical browser; the tool reports that rather than pretending.
Normal skill grants, read-only roles and planning restrictions still apply.

Supported: text, metrics, directed diagrams with zoom/search/node details,
line/bar/donut charts with exact data and CSV export, tables, and interactive
3D boxes/spheres/cones with orbit, zoom, reset, labels and connections.
3D uses a small software renderer, not a CAD engine; model imports, arbitrary
HTML/JavaScript, external assets and photorealistic rendering are not supported.
The validator checks structure and numeric/relationship consistency, not the
truth of the explanation. Prefer accurate content over decorative complexity.
