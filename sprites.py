"""
Pixel-art corgi, drawn entirely in code.

Every frame is built from simple shapes (ellipses, rectangles, triangles) on a
40 x 32 pixel grid, then each body part gets an automatic dark outline. This
keeps all the art in one editable place: tweak a number here and every frame
updates.

All frames face RIGHT. The app mirrors them for left-facing movement and
rotates the walk cycle for wall climbing.

Run `python export_sprites.py` to write PNGs, a sprite sheet and a GIF preview.
"""
from __future__ import annotations

W, H = 40, 32          # frame size in sprite pixels
GROUND = 30            # last row the paws fill (row 31 is the paw outline)

# ── Palette ────────────────────────────────────────────────────────────────
C = {
    "out":    (58, 34, 22, 255),     # outline, dark brown
    "or":     (232, 142, 62, 255),   # corgi orange
    "or_d":   (196, 106, 42, 255),   # orange shadow
    "or_l":   (247, 178, 108, 255),  # orange highlight
    "wh":     (255, 249, 238, 255),  # white fur
    "wh_d":   (226, 208, 186, 255),  # white fur shadow
    "pink":   (240, 146, 156, 255),  # inner ear
    "dark":   (34, 24, 20, 255),     # eyes, nose
    "hi":     (255, 255, 255, 255),  # eye shine
    "tongue": (236, 98, 112, 255),
    "mouth":  (122, 34, 46, 255),
    "blush":  (247, 150, 140, 255),
}


# ── A tiny pixel canvas ────────────────────────────────────────────────────
class Canvas:
    def __init__(self, w: int = W, h: int = H):
        self.w, self.h = w, h
        self.px = [[None] * w for _ in range(h)]

    def copy(self) -> "Canvas":
        c = Canvas(self.w, self.h)
        c.px = [row[:] for row in self.px]
        return c

    def get(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.px[y][x]
        return None

    def set(self, x, y, col):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y][x] = col

    def rect(self, x0, y0, x1, y1, col):
        for y in range(min(y0, y1), max(y0, y1) + 1):
            for x in range(min(x0, x1), max(x0, x1) + 1):
                self.set(x, y, col)

    def ellipse(self, cx, cy, rx, ry, col):
        rx2, ry2 = rx + 0.5, ry + 0.5
        for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
            for x in range(int(cx - rx) - 1, int(cx + rx) + 2):
                if ((x - cx) / rx2) ** 2 + ((y - cy) / ry2) ** 2 <= 1.0:
                    self.set(x, y, col)

    def poly(self, pts, col):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        for y in range(int(min(ys)), int(max(ys)) + 1):
            for x in range(int(min(xs)), int(max(xs)) + 1):
                if _inside(x, y, pts):
                    self.set(x, y, col)

    def each(self):
        for y in range(self.h):
            for x in range(self.w):
                if self.px[y][x] is not None:
                    yield x, y, self.px[y][x]

    def recolor(self, fn):
        """fn(x, y, col) -> new colour (or the same)."""
        for x, y, col in list(self.each()):
            self.px[y][x] = fn(x, y, col)

    def shade(self, base="or", light="or_l", dark="or_d"):
        """Highlight the top edge and shadow the bottom edge of `base` areas."""
        b, l, d = C[base], C[light], C[dark]
        src = self.copy()
        for x, y, col in src.each():
            if col != b:
                continue
            if src.get(x, y - 1) is None:
                self.px[y][x] = l
            elif src.get(x, y + 1) is None:
                self.px[y][x] = d

    def outlined(self, col=C["out"]) -> "Canvas":
        out = self.copy()
        for y in range(self.h):
            for x in range(self.w):
                if self.px[y][x] is None and any(
                    self.get(x + dx, y + dy) is not None
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                ):
                    out.px[y][x] = col
        return out

    def paste(self, other: "Canvas", dx=0, dy=0):
        for x, y, col in other.each():
            self.set(x + dx, y + dy, col)

    def flipped(self) -> "Canvas":
        c = Canvas(self.w, self.h)
        c.px = [row[::-1] for row in self.px]
        return c

    def rotated_ccw(self) -> "Canvas":
        """Rotate 90° counter-clockwise: right side → top, bottom → right."""
        c = Canvas(self.h, self.w)
        for x, y, col in self.each():
            c.set(y, self.w - 1 - x, col)
        return c

    def to_rgba(self) -> bytes:
        out = bytearray()
        for row in self.px:
            for col in row:
                out.extend(col if col else (0, 0, 0, 0))
        return bytes(out)


def _inside(x, y, pts):
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y):
            xcross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xcross:
                inside = not inside
        j = i
    return inside


def layer(draw) -> Canvas:
    """Draw a body part on its own canvas, then give it an outline."""
    c = Canvas()
    draw(c)
    return c.outlined()


# ── Body parts ─────────────────────────────────────────────────────────────
def _ear_shapes(cx, hy, ears):
    """Return (far_ear, near_ear) triangles for the ear style."""
    if ears == "up":
        far = [(cx - 7, hy - 1), (cx - 2, hy - 4), (cx - 6, hy - 10)]
        near = [(cx - 4, hy - 3), (cx + 2, hy - 4), (cx - 1, hy - 11)]
    elif ears == "back":        # flattened backwards (running, being held)
        far = [(cx - 3, hy - 1), (cx, hy - 3), (cx - 10, hy - 3)]
        near = [(cx - 2, hy - 3), (cx + 2, hy - 4), (cx - 10, hy - 5)]
    else:                       # "down": droopy and sad
        far = [(cx - 4, hy - 2), (cx - 1, hy - 4), (cx - 8, hy + 2)]
        near = [(cx - 3, hy - 3), (cx + 1, hy - 4), (cx - 9, hy + 1)]
    return far, near


def _shrink(tri, k=0.45):
    mx = sum(p[0] for p in tri) / 3
    my = sum(p[1] for p in tri) / 3
    return [(mx + (x - mx) * k, my + (y - my) * k + 0.6) for x, y in tri]


def draw_head(img: Canvas, cx, hy, eyes="open", mouth="smile", ears="up",
              blush=False):
    far_ear, near_ear = _ear_shapes(cx, hy, ears)
    upright = ears == "up"

    def ear(tri, col, inner):
        def d(c):
            c.poly(tri, C[col])
            c.shade()
            if inner:
                c.poly(_shrink(tri), C["pink"])
        return d

    img.paste(layer(ear(far_ear, "or_d", False)))
    if not upright:             # flat ears tuck behind the head
        img.paste(layer(ear(near_ear, "or", False)))

    def head(c):
        c.ellipse(cx, hy, 6, 5, C["or"])
        c.ellipse(cx + 6, hy + 3, 3, 2, C["wh"])            # muzzle
        c.shade()
        # white blaze on the front of the face and the cheek
        c.recolor(lambda x, y, col: C["wh"]
                  if col in (C["or"], C["or_l"], C["or_d"])
                  and ((x >= cx + 4 and y >= hy - 1) or y >= hy + 3)
                  else col)
    img.paste(layer(head))

    if upright:
        img.paste(layer(ear(near_ear, "or", True)))

    # Face details (no outline)
    ex, ey = cx + 3, hy - 1
    if eyes == "open":
        img.rect(ex, ey, ex + 1, ey + 1, C["dark"])
        img.set(ex + 1, ey, C["hi"])
    elif eyes == "wide":
        img.rect(ex, ey - 1, ex + 1, ey + 1, C["dark"])
        img.set(ex + 1, ey - 1, C["hi"])
    elif eyes == "closed":
        img.set(ex - 1, ey + 1, C["dark"])
        img.set(ex, ey + 1, C["dark"])
        img.set(ex + 1, ey + 1, C["dark"])
    elif eyes == "happy":       # ^ ^
        img.set(ex - 1, ey + 1, C["dark"])
        img.set(ex, ey, C["dark"])
        img.set(ex + 1, ey, C["dark"])
        img.set(ex + 2, ey + 1, C["dark"])
    elif eyes == "sad":
        img.rect(ex, ey + 1, ex + 1, ey + 1, C["dark"])
        img.set(ex + 1, ey, C["dark"])
        img.set(ex - 1, ey - 2, C["out"])                   # worried brow
        img.set(ex, ey - 2, C["out"])
        img.set(ex + 1, ey - 3, C["out"])
    elif eyes == "dizzy":       # little x
        for d in (-1, 0, 1):
            img.set(ex + d, ey + d, C["dark"])
            img.set(ex + d, ey - d, C["dark"])

    if blush:
        img.set(cx + 1, hy + 2, C["blush"])
        img.set(cx + 2, hy + 2, C["blush"])

    # nose
    nx, ny = cx + 9, hy + 1
    img.set(nx - 1, ny, C["dark"])
    img.set(nx, ny, C["dark"])
    img.set(nx, ny + 1, C["dark"])

    # mouth
    if mouth in ("smile", "tongue"):
        img.set(cx + 8, hy + 3, C["out"])
        img.set(cx + 7, hy + 4, C["out"])
        img.set(cx + 6, hy + 4, C["out"])
    if mouth == "tongue":
        img.paste(layer(lambda c: c.rect(cx + 6, hy + 5, cx + 7, hy + 6,
                                         C["tongue"])))
    elif mouth == "open":
        img.paste(layer(lambda c: c.rect(cx + 6, hy + 4, cx + 8, hy + 5,
                                         C["mouth"])))
    elif mouth == "yawn":
        def yawn(c):
            c.rect(cx + 5, hy + 3, cx + 8, hy + 6, C["mouth"])
            c.rect(cx + 5, hy + 6, cx + 7, hy + 6, C["tongue"])
        img.paste(layer(yawn))


def _leg(c, x, top, bottom, upper, paw):
    c.rect(x, top, x + 2, bottom, upper)
    c.rect(x, bottom - 1, x + 2, bottom, paw)


def draw_stand(legs=((0, 0),) * 4, body_dy=0, head_dy=0, head_dx=0,
               tail_dy=0, eyes="open", mouth="smile", ears="up",
               blush=False) -> Canvas:
    """Standing / walking pose.

    legs: (dx, lift) for back-far, back-near, front-far, front-near.
    """
    img = Canvas()
    cy = 21 + body_dy
    top = cy + 2
    xs = (8, 12, 20, 24)
    (bf, bn, ff, fn) = legs

    img.paste(layer(lambda c: (
        _leg(c, xs[0] + bf[0], top, GROUND - bf[1], C["or_d"], C["wh_d"]),
        _leg(c, xs[2] + ff[0], top, GROUND - ff[1], C["wh_d"], C["wh_d"]),
    )))
    img.paste(layer(lambda c: (
        _leg(c, xs[1] + bn[0], top, GROUND - bn[1], C["or"], C["wh"]),
        _leg(c, xs[3] + fn[0], top, GROUND - fn[1], C["wh"], C["wh"]),
    )))

    def tail(c):
        c.ellipse(6, cy - 3 + tail_dy, 2, 2, C["or"])
        c.shade()
        c.set(4, cy - 3 + tail_dy, C["wh"])
        c.set(4, cy - 4 + tail_dy, C["wh"])
    img.paste(layer(tail))

    def body(c):
        c.ellipse(17, cy, 10, 5, C["or"])
        c.ellipse(25, cy - 3, 3, 4, C["or"])                  # neck
        c.shade()
        c.recolor(lambda x, y, col: C["wh"]
                  if y >= cy + 3 or (x >= 24 and y >= cy - 2)
                  or (x <= 9 and y >= cy + 1)
                  else col)
        c.recolor(lambda x, y, col: C["wh_d"]
                  if col == C["wh"] and y == cy + 5 else col)
    img.paste(layer(body))

    draw_head(img, 29 + head_dx, 12 + body_dy + head_dy, eyes, mouth, ears,
              blush)
    return img


def draw_sit(eyes="open", mouth="smile", ears="up", tail_dy=0, head_dy=0,
             blush=False) -> Canvas:
    img = Canvas()

    def tail(c):
        c.ellipse(8, 26 + tail_dy, 2, 2, C["or"])
        c.shade()
        c.set(6, 26 + tail_dy, C["wh"])
    img.paste(layer(tail))

    img.paste(layer(lambda c: c.rect(21, 22, 23, GROUND, C["wh_d"])))

    def body(c):
        c.ellipse(14, 25, 6, 4, C["or"])                       # haunch
        c.ellipse(21, 20, 5, 7, C["or"])                       # torso
        c.shade()
        c.recolor(lambda x, y, col: C["wh"]
                  if (x >= 23 and y >= 15) or y >= 28 else col)
    img.paste(layer(body))

    img.paste(layer(lambda c: c.ellipse(17, 29, 2, 1, C["wh"])))   # back paw
    img.paste(layer(lambda c: c.rect(24, 22, 26, GROUND, C["wh"])))  # front leg

    draw_head(img, 28, 11 + head_dy, eyes, mouth, ears, blush)
    return img


def draw_lie(eyes="closed", mouth="smile", ears="back", breathe=0) -> Canvas:
    img = Canvas()

    def tail(c):
        c.ellipse(5, 25, 2, 2, C["or"])
        c.shade()
        c.set(3, 25, C["wh"])
    img.paste(layer(tail))

    def body(c):
        c.ellipse(15, 26 - breathe * 0.5, 10, 4 + breathe * 0.5, C["or"])
        c.shade()
        c.recolor(lambda x, y, col: C["wh"] if y >= 29 else col)
    img.paste(layer(body))

    img.paste(layer(lambda c: c.rect(24, 28, 31, GROUND, C["wh"])))  # paws

    draw_head(img, 28, 23, eyes, mouth, ears)
    return img


# ── Animations ─────────────────────────────────────────────────────────────
STEP = [(1, 0), (0, 1), (-1, 0), (0, 0)]


def _walk_legs(p, stride=1):
    a = STEP[p % 4]
    b = STEP[(p + 2) % 4]
    a = (a[0] * stride, a[1])
    b = (b[0] * stride, b[1])
    # diagonal pairs move together: back-near + front-far, back-far + front-near
    return (b, a, a, b)


def build_animations() -> dict[str, list[Canvas]]:
    A: dict[str, list[Canvas]] = {}

    A["idle"] = [
        draw_stand(tail_dy=t, eyes=e)
        for t, e in [(0, "open"), (-1, "open"), (0, "open"),
                     (-1, "open"), (0, "open"), (-1, "closed")]
    ]
    A["pant"] = [
        draw_stand(tail_dy=t, mouth=m, head_dy=h)
        for t, m, h in [(0, "tongue", 0), (-1, "open", 1)]
    ]
    A["walk"] = [
        draw_stand(legs=_walk_legs(p), head_dy=[0, 1, 0, 1][p],
                   tail_dy=[0, -1, 0, -1][p])
        for p in range(4)
    ]
    A["walk_sad"] = [
        draw_stand(legs=_walk_legs(p), head_dy=1 + [0, 1, 0, 1][p],
                   tail_dy=2, eyes="sad", ears="down")
        for p in range(4)
    ]
    A["run"] = [
        draw_stand(legs=((-2, 1), (-2, 0), (2, 0), (2, 1)), ears="back",
                   mouth="tongue", tail_dy=-1),
        draw_stand(legs=((0, 1), (-1, 1), (1, 1), (0, 1)), ears="back",
                   mouth="tongue", body_dy=-1),
        draw_stand(legs=((1, 0), (2, 1), (-2, 1), (-1, 0)), ears="back",
                   mouth="tongue", tail_dy=-1),
        draw_stand(legs=((0, 1), (1, 1), (-1, 1), (0, 1)), ears="back",
                   mouth="tongue", body_dy=-1),
    ]
    A["sad"] = [
        draw_stand(eyes="sad", ears="down", head_dy=1, tail_dy=2),
        draw_stand(eyes="sad", ears="down", head_dy=2, tail_dy=2),
    ]
    A["excited"] = [
        draw_stand(eyes="happy", mouth="tongue", tail_dy=0),
        draw_stand(legs=((0, 0), (0, 0), (1, 2), (1, 3)), body_dy=-1,
                   eyes="happy", mouth="tongue", tail_dy=-2),
        draw_stand(eyes="happy", mouth="tongue", tail_dy=0),
        draw_stand(legs=((0, 0), (0, 0), (1, 3), (1, 2)), body_dy=-1,
                   eyes="happy", mouth="open", tail_dy=-2),
    ]
    A["drag"] = [
        draw_stand(legs=((-1, 0), (-1, 0), (1, 0), (1, 0)), body_dy=-4,
                   eyes="wide", mouth="open", ears="back"),
        draw_stand(legs=((1, 0), (0, 0), (0, 0), (-1, 0)), body_dy=-4,
                   eyes="wide", mouth="open", ears="back", tail_dy=1),
    ]
    A["fall"] = [
        draw_stand(legs=((-2, 0), (-1, 0), (1, 0), (2, 0)), body_dy=-3,
                   eyes="wide", mouth="open", ears="up"),
        draw_stand(legs=((-2, 1), (-1, 1), (1, 1), (2, 1)), body_dy=-3,
                   eyes="wide", mouth="open", ears="back"),
    ]
    A["land"] = [
        draw_stand(legs=((-1, 0), (0, 0), (0, 0), (1, 0)), body_dy=2,
                   eyes="closed", ears="back"),
    ]
    A["dizzy"] = [
        draw_stand(eyes="dizzy", mouth="open", head_dy=0, ears="back"),
        draw_stand(eyes="dizzy", mouth="tongue", head_dy=1, ears="back"),
    ]
    A["sit"] = [
        draw_sit(tail_dy=0), draw_sit(tail_dy=-1),
        draw_sit(tail_dy=0), draw_sit(tail_dy=-1, eyes="closed"),
    ]
    A["yawn"] = [
        draw_sit(mouth="open", eyes="open"),
        draw_sit(mouth="yawn", eyes="closed", head_dy=-1),
        draw_sit(mouth="yawn", eyes="closed", head_dy=-1),
        draw_sit(mouth="open", eyes="closed"),
        draw_sit(mouth="smile", eyes="closed"),
    ]
    A["bark"] = [
        draw_sit(mouth="open", eyes="wide", head_dy=-1, ears="up"),
        draw_sit(mouth="smile", eyes="open"),
    ]
    A["happy"] = [
        draw_sit(eyes="happy", mouth="tongue", tail_dy=0, blush=True),
        draw_sit(eyes="happy", mouth="tongue", tail_dy=-2, blush=True,
                 head_dy=1),
    ]
    A["sleep"] = [draw_lie(breathe=0), draw_lie(breathe=1)]
    # Climbing: walk cycle rotated so the head points up and paws grip a
    # wall on the RIGHT. The app mirrors it for the left wall.
    A["climb"] = [f.rotated_ccw() for f in A["walk"]]
    return A


# Where the head is (for overlays like Zzz, sweat and hearts), facing right.
HEAD = {"stand": (29, 12), "sit": (28, 11), "lie": (28, 23), "climb": (12, 10)}

POSE = {
    "idle": "stand", "pant": "stand", "walk": "stand", "walk_sad": "stand",
    "run": "stand", "sad": "stand", "excited": "stand", "drag": "stand",
    "fall": "stand", "land": "stand", "dizzy": "stand", "sit": "sit",
    "yawn": "sit", "bark": "sit", "happy": "sit", "sleep": "lie",
    "climb": "climb",
}

# Frames per second for each animation, and whether it loops.
FPS = {
    "idle": 3, "pant": 4, "walk": 8, "walk_sad": 5, "run": 12, "sad": 1.5,
    "excited": 8, "drag": 5, "fall": 8, "land": 1, "dizzy": 3, "sit": 2,
    "yawn": 3, "bark": 4, "happy": 4, "sleep": 1.2, "climb": 8,
}
ONCE = {"yawn", "land"}
