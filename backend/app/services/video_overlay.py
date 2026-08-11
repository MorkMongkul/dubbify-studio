"""
app/services/video_overlay.py
Renders burned-in Khmer subtitle images for video export.

The local ffmpeg build has no libass/freetype support (no `subtitles`/
`drawtext` filters), so subtitle text is rendered to transparent PNGs with
Pillow instead and composited onto the video via ffmpeg's `overlay` filter,
which is available regardless of how ffmpeg was built. Pillow is built with
raqm here, so complex Khmer glyph shaping (subscripts, vowel reordering)
renders correctly — plain freetype text layout would not.

Each rendered PNG is sized to the subtitle overlay's own box (in real video
pixels, derived from its stored x/y/width/height fractions) so it composites
through the exact same generic "positioned image" path as a dropped logo —
see audio_extractor.mix_dubbed_audio.
"""
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

FONT_PATH = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansKhmer-Bold.ttf"

_COLOR_NAMES = {
    "white": (255, 255, 255, 255),
    "yellow": (255, 224, 32, 255),
    "black": (0, 0, 0, 255),
}


def _resolve_color(color: str, alpha: int = 255) -> tuple:
    r, g, b, _ = _COLOR_NAMES.get(color.lower(), _COLOR_NAMES["white"])
    return (r, g, b, alpha)


# Caption-chip background — translucent so it reads as a soft backing plate
# behind the text rather than a flat opaque block.
_BACKGROUND_ALPHA = 190

# Fitting the line to its box. Text used to be drawn as one unwrapped line, so
# anything longer than the box simply ran off the edge and was cropped by the
# PNG boundary. Now it wraps, and if it still doesn't fit the font shrinks.
_MIN_FONT_SCALE = 0.45   # never shrink past this fraction of the chosen size
_FONT_STEP      = 0.92   # multiplicative step while searching for a fit
_LINE_SPACING   = 1.18

# The subtitle overlay's box supplies the text COLUMN WIDTH and a bottom anchor
# line; the rendered block's height follows the text. One box has to serve every
# segment in the film, and translated lines vary wildly in length — sizing each
# segment down to a hand-drawn rectangle would make the font jump around from
# line to line. Instead the font stays exactly as configured and the block grows
# upward, the way real subtitles behave.
#
# Only when a block would swallow the frame does the font shrink as a backstop.
_MAX_BLOCK_FRACTION = 0.40   # of video height


def _wrap_to_width(text: str, font, draw, stroke_width: int, max_w: int) -> list[str]:
    """
    Greedy wrap on whitespace.

    A single run longer than max_w is kept whole rather than split: Khmer
    writes without spaces between words and stacks subscripts under the
    baseline, so breaking mid-run would cut a glyph cluster apart. The caller
    shrinks the font for those instead.
    """
    words = text.split()
    if not words:
        return []

    def width_of(s: str) -> int:
        bbox = draw.textbbox((0, 0), s, font=font, stroke_width=stroke_width)
        return bbox[2] - bbox[0]

    lines, current = [], words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if width_of(candidate) <= max_w:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fit_text(text: str, requested_size: int, max_w: int, max_h: int, draw):
    """
    Largest font size (<= requested_size) whose wrapped text fits the box.

    Returns (font, lines, stroke_width, line_height, ascent). Falls back to the
    floor size when even that overflows — clipping a very long line at 45% is
    still better than clipping it at full size.
    """
    floor = max(12, int(requested_size * _MIN_FONT_SCALE))
    size = max(requested_size, floor)

    while True:
        stroke = max(2, size // 14)
        font = ImageFont.truetype(str(FONT_PATH), size, layout_engine=ImageFont.Layout.RAQM)
        lines = _wrap_to_width(text, font, draw, stroke, max_w) or [text]

        ascent, descent = font.getmetrics()
        boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke) for line in lines]
        # Khmer stacks marks above and below, which can exceed the font's own
        # metrics — take whichever is taller so lines never collide.
        tallest = max(b[3] - b[1] for b in boxes)
        line_height = int(max(ascent + descent + 2 * stroke, tallest) * _LINE_SPACING)
        widest = max(b[2] - b[0] for b in boxes)

        fits = widest <= max_w and line_height * len(lines) <= max_h
        if fits or size <= floor:
            return font, lines, stroke, line_height, ascent

        # int(size * 0.92) stops moving at small sizes — always drop at least 1px
        size = max(floor, min(size - 1, int(size * _FONT_STEP)))


def render_subtitle_pngs(
    segments: list,
    box: dict,
    video_width: int,
    video_height: int,
    out_dir: Path,
    font_size: int = 42,
    color: str = "white",
    outline_color: str = "black",
    background_color: str = "",
) -> list[dict]:
    """
    Renders one transparent PNG per segment with non-empty khmer_text.

    The box supplies the text COLUMN WIDTH and, via its bottom edge
    (y + height), the anchor line every subtitle sits on. Each segment's PNG is
    only as tall as its own wrapped text and is placed so its bottom lands on
    that anchor — so longer lines grow UPWARD at an unchanged font size instead
    of being squeezed into a fixed rectangle. `font_size` is therefore honoured
    literally, and is identical on every segment of the film.

    box: {x, y, width, height} fractions (0-1) of the video's own dimensions,
    exactly as stored on the subtitle Overlay row.

    Returns: [{start_time, end_time, media_path, x, y, width, height}, ...],
    one per segment with non-empty text — shaped identically to an image
    overlay entry so the caller can composite both through one code path.
    `y`/`height` vary per segment (that is the auto-height).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    fill = _resolve_color(color)
    stroke_fill = _resolve_color(outline_color)

    box_w = max(1, round(video_width * box["width"]))
    # Leave a little breathing room so descenders and the stroke never touch
    # the PNG edge (and so a background chip has somewhere to sit).
    inset_w = max(1, box_w - round(box_w * 0.04))
    # Bottom edge of the configured box = the anchor line the text rests on.
    anchor_bottom = round(video_height * min(1.0, box["y"] + box["height"]))
    # Backstop only: a block taller than this shrinks rather than covering the
    # frame. Normal 1-3 line subtitles never reach it.
    max_block_h = max(1, round(video_height * _MAX_BLOCK_FRACTION))
    results = []

    # Scratch surface for measuring — Pillow needs a draw context to compute
    # text extents, and the fit search runs before the real image exists.
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    for seg in segments:
        text = (getattr(seg, "khmer_text", "") or "").strip()
        if not text:
            continue

        font, lines, stroke_width, line_height, ascent = _fit_text(
            text, font_size, inset_w, max_block_h, measure
        )

        # The PNG is sized to THIS segment's text, not to the box: block height
        # plus padding for the stroke/descenders (and the chip, when enabled).
        pad_y = max(2, round(font.size * 0.22))
        block_h = line_height * len(lines)
        img_h = block_h + pad_y * 2

        img = Image.new("RGBA", (box_w, img_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        block_top = pad_y
        centre_x = box_w / 2
        # anchor="ms" = horizontally centred on a shared baseline, so every
        # line sits on the same grid regardless of which marks it carries.
        baselines = [block_top + ascent + i * line_height for i in range(len(lines))]

        if background_color:
            pad_x = round(font.size * 0.35)
            chip_pad_y = round(font.size * 0.2)
            chip_fill = _resolve_color(background_color, _BACKGROUND_ALPHA)
            radius = round(font.size * 0.18)
            for line, baseline in zip(lines, baselines):
                bbox = draw.textbbox(
                    (centre_x, baseline), line, font=font, anchor="ms", stroke_width=stroke_width
                )
                draw.rounded_rectangle(
                    [
                        max(0, bbox[0] - pad_x), max(0, bbox[1] - chip_pad_y),
                        min(box_w, bbox[2] + pad_x), min(img_h, bbox[3] + chip_pad_y),
                    ],
                    radius=radius,
                    fill=chip_fill,
                )

        for line, baseline in zip(lines, baselines):
            draw.text(
                (centre_x, baseline), line, font=font, anchor="ms",
                fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill,
            )

        image_path = out_dir / f"sub_{seg.id}.png"
        img.save(image_path)
        # Bottom-anchored: the block's bottom edge lands on the anchor line and
        # it extends upward, clamped so a very tall block can't run off frame.
        top_px = max(0, min(anchor_bottom - img_h, video_height - img_h))
        results.append({
            "start_time": seg.start_time,
            "end_time": seg.end_time,
            "media_path": str(image_path),
            "x": box["x"],
            "y": top_px / video_height,
            "width": box["width"],
            "height": img_h / video_height,
        })

    logger.info(
        f"Rendered {len(results)} subtitle PNGs "
        f"(column {box_w}px @ {font_size}px, bottom-anchored at y={anchor_bottom}px)"
    )
    return results
