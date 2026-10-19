# Design a visual explanation

Give the page one main question and answer it in its title and summary.
Start an architecture explanation with a large diagram, not a row of invented
metrics. Reserve colour for categories and selection; labels carry meaning
without colour. Use explicit units and distinguish observed data, assumptions
and illustrative numbers. Every chart has an exact-value table; every diagram
has a textual list of nodes and directed connections.

The renderer uses a wide editorial layout, system typography, high-contrast
light/dark palettes, quiet separators and generous diagram surfaces. Do not
repeat the same story as several redundant charts. Lines show trends, bars
compare quantities, donuts show a small set of non-negative parts of a whole.
Use the same unit for every series on an axis. Prefer a table for precise
lookup, and a separate chart for a different unit.

In architecture diagrams, columns show flow stages and rows separate branches.
Leave space around back-edges and cross-links. Keep labels short and put full
explanations in node details. For a very large system, show an overview plus
focused subsystem diagrams instead of shrinking everything to unreadable text.

3D earns its space when position/depth matters: physical arrangements, layered
systems or a spatial network. Supply separated coordinates and meaningful
labels. It is an interactive model, never evidence of an actual physical scale
unless the source provides that scale. Avoid using depth to compare ordinary
bar heights. The scene has keyboard rotation, explicit zoom/reset controls,
no automatic motion, and a textual alternative below it.

Design basis (consulted 2026-10-19):
- Kvist's bundled `design` skill: visual hierarchy, 16px body text, keyboard
  focus, 44px touch controls, no horizontal page scroll, colour-independent cues.
- [W3C WAI: Complex images](https://www.w3.org/WAI/tutorials/images/complex/):
  accompany charts and diagrams with a short description and detailed data.
- [NN/g: Clutter-free charts](https://www.nngroup.com/articles/clutter-charts/):
  remove decoration that distracts from the data.
- [MDN: Optimizing canvas](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API/Tutorial/Optimizing_canvas):
  limit drawing work; redraw the small 3D scene on interaction or resize.
