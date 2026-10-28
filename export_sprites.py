"""Export the corgi's frames as PNGs, a sprite sheet and an animated GIF.

    pip install pillow
    python export_sprites.py            # writes to ./sprite_export/

To redraw a frame by hand: copy e.g. sprite_export/frames/idle_0.png into a
folder called custom_sprites/ next to main.py, edit it in Aseprite/Piskel,
and the pet will use your version instead of the generated one.
"""
from pathlib import Path

from PIL import Image

import sprites

OUT = Path(__file__).with_name("sprite_export")
SCALE = 6


def to_image(cv):
    return Image.frombytes("RGBA", (cv.w, cv.h), cv.to_rgba())


def main():
    anims = sprites.build_animations()
    (OUT / "frames").mkdir(parents=True, exist_ok=True)

    # 1. Individual frames at native size (good for editing)
    for name, frames in anims.items():
        for i, cv in enumerate(frames):
            to_image(cv).save(OUT / "frames" / f"{name}_{i}.png")

    # 2. Labelled sprite sheet, one row per animation
    cell = 44
    cols = max(len(f) for f in anims.values())
    sheet = Image.new("RGBA", (cols * cell * SCALE, len(anims) * cell * SCALE),
                      (214, 228, 240, 255))
    for r, (name, frames) in enumerate(anims.items()):
        for c, cv in enumerate(frames):
            im = to_image(cv).resize((cv.w * SCALE, cv.h * SCALE),
                                     Image.NEAREST)
            sheet.alpha_composite(im, (c * cell * SCALE + 2 * SCALE,
                                       r * cell * SCALE + 2 * SCALE))
    sheet.save(OUT / "sprite_sheet.png")

    # 3. Animated GIF showing every animation in turn
    gif_frames, durations = [], []
    for name, frames in anims.items():
        fps = sprites.FPS.get(name, 4)
        loops = 1 if name in sprites.ONCE else max(1, round(2.4 * fps
                                                            / len(frames)))
        for _ in range(loops):
            for cv in frames:
                bg = Image.new("RGBA", (cell * SCALE, cell * SCALE),
                               (214, 228, 240, 255))
                im = to_image(cv).resize((cv.w * SCALE, cv.h * SCALE),
                                         Image.NEAREST)
                bg.alpha_composite(im, ((cell - cv.w) // 2 * SCALE,
                                        (cell - cv.h) * SCALE))
                gif_frames.append(bg.convert("P", palette=Image.ADAPTIVE))
                durations.append(int(1000 / fps))
    gif_frames[0].save(OUT / "preview.gif", save_all=True,
                       append_images=gif_frames[1:], duration=durations,
                       loop=0, disposal=2)
    print(f"Wrote {sum(len(f) for f in anims.values())} frames to {OUT}")


if __name__ == "__main__":
    main()
