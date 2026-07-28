"""One-off script: generate the new "Silo" logo icon set, replacing the old
PayEnvelope mail-envelope mark. Uses the same brand gradient (#6366F1 ->
#8B5CF6, 135deg) and white line-art style as the original icons, so the
rebrand is visually consistent with the rest of the UI (buttons, glows, etc.
all still use --gradient-brand)."""

from PIL import Image, ImageDraw

OUT_DIR = "frontend/icons"

COLOR_START = (99, 102, 241)   # #6366F1
COLOR_END = (139, 92, 246)     # #8B5CF6


def diagonal_gradient(size, c0, c1):
    """135deg linear gradient (top-left -> bottom-right)."""
    base = Image.new("RGB", (size, size), c0)
    top = Image.new("RGB", (size, size), c1)
    mask = Image.new("L", (size, size))
    mask_data = []
    for y in range(size):
        for x in range(size):
            # projection along the diagonal, normalized 0..255
            t = (x + y) / (2 * (size - 1)) if size > 1 else 0
            mask_data.append(int(t * 255))
    mask.putdata(mask_data)
    return Image.composite(top, base, mask)


def rounded_mask(size, radius_ratio=0.22):
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=255)
    return mask


def draw_silo(draw: ImageDraw.ImageDraw, size: int, stroke_ratio: float = 0.055):
    """Draws a simple grain-silo glyph: a peaked roof over a ribbed cylinder,
    scaled to the given icon size, in white."""
    w = size
    stroke = max(1, round(w * stroke_ratio))
    white = (255, 255, 255, 255)

    cx = w / 2
    roof_top_y = w * 0.16
    roof_base_y = w * 0.38
    body_left = w * 0.27
    body_right = w * 0.73
    body_bottom = w * 0.86
    corner_r = w * 0.05

    # Roof (peaked triangle-ish silo cap)
    draw.line(
        [(cx, roof_top_y), (body_left, roof_base_y)],
        fill=white, width=stroke, joint="curve",
    )
    draw.line(
        [(cx, roof_top_y), (body_right, roof_base_y)],
        fill=white, width=stroke, joint="curve",
    )

    # Body outline (rounded rectangle -> cylinder silhouette)
    draw.rounded_rectangle(
        [body_left, roof_base_y, body_right, body_bottom],
        radius=corner_r, outline=white, width=stroke,
    )

    # Ribbed bands across the body
    band1_y = roof_base_y + (body_bottom - roof_base_y) * 0.38
    band2_y = roof_base_y + (body_bottom - roof_base_y) * 0.68
    inset = stroke * 0.6
    draw.line([(body_left + inset, band1_y), (body_right - inset, band1_y)], fill=white, width=max(1, round(stroke * 0.75)))
    draw.line([(body_left + inset, band2_y), (body_right - inset, band2_y)], fill=white, width=max(1, round(stroke * 0.75)))


def make_icon(size: int, rounded: bool, filename: str, icon_scale: float = 0.5):
    bg = diagonal_gradient(size, COLOR_START, COLOR_END).convert("RGBA")

    icon_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon_size = int(size * icon_scale)
    icon_img = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
    draw_silo(ImageDraw.Draw(icon_img), icon_size)
    offset = ((size - icon_size) // 2, (size - icon_size) // 2)
    icon_layer.paste(icon_img, offset, icon_img)

    composed = Image.alpha_composite(bg, icon_layer)

    if rounded:
        mask = rounded_mask(size)
        final = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        final.paste(composed, (0, 0), mask)
    else:
        final = composed

    final.save(f"{OUT_DIR}/{filename}")
    print(f"wrote {OUT_DIR}/{filename} ({size}x{size}, rounded={rounded})")


if __name__ == "__main__":
    make_icon(32, rounded=True, filename="favicon-32.png", icon_scale=0.6)
    make_icon(192, rounded=True, filename="icon-192.png", icon_scale=0.5)
    make_icon(512, rounded=True, filename="icon-512.png", icon_scale=0.5)
    # Maskable: full-bleed background, icon kept within the ~safe zone (40% radius)
    make_icon(512, rounded=False, filename="icon-maskable-512.png", icon_scale=0.4)
