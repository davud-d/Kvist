# Draws the three squirrel frames (idle, tail up, tail wag) as 32x32 pixel art: shapes on an
# integer grid, then an automatic 1px outline, so every frame has the same clean edge.
# Run: python3 make_sprites.py   (needs Pillow)
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 32
OUT = Path(__file__).parent
INK = (43, 24, 28, 255)
FUR = (232, 119, 46, 255)
FUR_DARK = (190, 83, 30, 255)
FUR_LIGHT = (246, 168, 85, 255)
CREAM = (251, 231, 198, 255)
EYE = (26, 16, 32, 255)


def frame(tail_dx: int, tail_dy: int, blink: bool = False) -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def blob(box, fill):
        d.ellipse(box, fill=fill)

    x, y = tail_dx, tail_dy
    # tail: a tall curl behind the body, darker at the back, lighter inside
    blob((2 + x, 8 + y, 14 + x, 29 + y), FUR_DARK)
    blob((4 + x, 3 + y, 15 + x, 17 + y), FUR_DARK)
    blob((5 + x, 10 + y, 13 + x, 27 + y), FUR)
    blob((6 + x, 5 + y, 14 + x, 15 + y), FUR)
    blob((8 + x, 7 + y, 12 + x, 13 + y), FUR_LIGHT)
    # body and belly
    blob((11, 16, 25, 30), FUR)
    blob((19, 18, 25, 29), CREAM)
    # head and ears
    blob((16, 7, 28, 19), FUR)
    d.polygon([(16, 10), (17, 2), (22, 8)], fill=FUR)
    d.polygon([(22, 8), (26, 2), (28, 11)], fill=FUR)
    d.polygon([(18, 6), (18, 4), (20, 7)], fill=CREAM)
    # face: cheek, eye, nose
    blob((22, 13, 29, 19), CREAM)
    d.point((24, 12), fill=EYE)
    if not blink:
        d.point((24, 11), fill=EYE)
    d.point((28, 14), fill=EYE)
    d.point((28, 15), fill=EYE)
    # feet
    d.rectangle((14, 28, 18, 30), fill=FUR_DARK)
    d.rectangle((21, 28, 25, 30), fill=FUR_DARK)

    # one-pixel outline around everything that was drawn
    solid = img.load()
    outlined = img.copy()
    out = outlined.load()
    for yy in range(SIZE):
        for xx in range(SIZE):
            if solid[xx, yy][3]:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = xx + dx, yy + dy
                if 0 <= nx < SIZE and 0 <= ny < SIZE and solid[nx, ny][3]:
                    out[xx, yy] = INK
                    break
    return outlined


for i, (dx, dy, blink) in enumerate([(0, 0, False), (-1, -1, False), (-2, 0, True)], start=1):
    frame(dx, dy, blink).save(OUT / f"squirrel-{i}.png")
print("ok")
