"""CAD-14 deterministic placement math / RGBA byte mapping; no Qt or Krita imports."""
from fractions import Fraction

MAX_DIMENSION = 32768
MODES = ("keep_size", "fit_canvas", "insert_at_offset")


class PlacementError(ValueError):
    pass


def integer(value, label, minimum=0):
    # JSON integer only: in particular bool, float and numeric strings are not ints.
    if type(value) is not int or not minimum <= value <= MAX_DIMENSION:
        raise PlacementError("{0} 必须是 {1}..{2} 的整数（不接受 bool/float/字符串）".format(
            label, minimum, MAX_DIMENSION))
    return value


def resolve_mode(mode, fit_canvas, offset_x, offset_y):
    x = integer(offset_x, "offset_x")
    y = integer(offset_y, "offset_y")
    if fit_canvas is not None and type(fit_canvas) is not bool:
        raise PlacementError("fit_canvas 兼容别名必须是 bool")
    legacy = "insert_at_offset" if x or y else "keep_size"
    if mode is None:
        mode = "fit_canvas" if fit_canvas is True else legacy
    if not isinstance(mode, str) or mode not in MODES:
        raise PlacementError("mode 仅支持 keep_size/fit_canvas/insert_at_offset；crop_at_bbox 尚未实现")
    if fit_canvas is not None and (mode == "fit_canvas") != fit_canvas:
        raise PlacementError("mode 与 fit_canvas 别名冲突")
    if mode != "insert_at_offset" and (x or y):
        raise PlacementError("非零 offset 仅用于 insert_at_offset；不与居中/原点行为混用")
    return mode, x, y


def plan_placement(source_width, source_height, canvas_width, canvas_height,
                   mode=None, offset_x=0, offset_y=0, fit_canvas=None):
    mode, x, y = resolve_mode(mode, fit_canvas, offset_x, offset_y)
    sw = integer(source_width, "source_width", 1)
    sh = integer(source_height, "source_height", 1)
    cw = integer(canvas_width, "canvas_width", 1)
    ch = integer(canvas_height, "canvas_height", 1)
    scale = Fraction(1)
    w, h = sw, sh
    if mode == "fit_canvas":
        # Never upscale. Exact rational contain scale, then floor each axis.
        scale = min(Fraction(1), Fraction(cw, sw), Fraction(ch, sh))
        w = max(1, sw * scale.numerator // scale.denominator)
        h = max(1, sh * scale.numerator // scale.denominator)
        x, y = (cw - w) // 2, (ch - h) // 2
    if x + w > cw or y + h > ch:
        raise PlacementError("图层越界: {0}x{1}@({2},{3}) 超出画布 {4}x{5}".format(
            w, h, x, y, cw, ch))
    return {
        "version": 1, "mode": mode,
        "source_size": [sw, sh], "canvas_size": [cw, ch],
        "output_size": [w, h], "origin": [x, y],
        "source_rect": [0, 0, sw, sh], "destination_rect": [x, y, w, h],
        "nominal_scale": [scale.numerator, scale.denominator],
        "edge_transform": {"scale_x": [w, sw], "scale_y": [h, sh], "translate": [x, y]},
        "rounding": "floor_dimensions_min_1_center_floor",
        "sampling": "nearest_pixel_center_floor",
        "upscale": False, "crop": False,
        "transparent_margins": [x, y, cw - x - w, ch - y - h],
    }


def packed_rgba(raw, width, height, bytes_per_line):
    """Strip row padding, preserving the existing RGBA8888 byte order exactly."""
    w = integer(width, "width", 1)
    h = integer(height, "height", 1)
    if type(bytes_per_line) is not int or bytes_per_line < w * 4:
        raise PlacementError("QImage bytesPerLine 小于 RGBA 行长度")
    if len(raw) < bytes_per_line * h:
        raise PlacementError("QImage 像素缓冲区长度不足")
    return b"".join(bytes(raw[r * bytes_per_line:r * bytes_per_line + w * 4])
                    for r in range(h))


def resize_rgba(data, plan):
    """Nearest neighbour with explicit pixel-center sampling, no alpha blending.

    dest (u,v) reads source (floor((2u+1)*sw/(2w)), floor((2v+1)*sh/(2h))).
    Hidden RGB under alpha=0 and all channels are copied without premultiplication.
    """
    sw, sh = plan["source_size"]
    w, h = plan["output_size"]
    if len(data) != sw * sh * 4:
        raise PlacementError("RGBA 像素缓冲区长度不符")
    if (sw, sh) == (w, h):
        return data
    xs = [((2 * u + 1) * sw // (2 * w)) * 4 for u in range(w)]
    out = bytearray(w * h * 4)
    for v in range(h):
        row = ((2 * v + 1) * sh // (2 * h)) * sw * 4
        for u, sx in enumerate(xs):
            start = (v * w + u) * 4
            out[start:start + 4] = data[row + sx:row + sx + 4]
    return bytes(out)
