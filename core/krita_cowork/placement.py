"""CAD-14 deterministic placement math / RGBA byte mapping; no Qt or Krita imports."""
from fractions import Fraction
import os
import hashlib
import stat
import struct
import time
import zlib

# Local admission limits, not an RSS cap or hard realtime/cancellation promise.
MAX_SOURCE_PIXELS = 16 * 1024 * 1024
MAX_DECODED_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_PIXELS = 4 * 1024 * 1024
MAX_PNG_BYTES = 32 * 1024 * 1024
MAX_WORKING_BYTES = 256 * 1024 * 1024
MAX_PNG_CHUNKS = 4096
PREPROCESS_SECONDS = 5.0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

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


class PreprocessBudget:
    """Cooperative elapsed-time checks; cannot interrupt Qt or a bridge callback."""

    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.deadline = self.clock() + PREPROCESS_SECONDS

    def check(self, stage):
        if self.clock() >= self.deadline:
            raise PlacementError("图像预处理时间预算 {0:g}s 已耗尽（{1}），不开始新图层写入".format(
                PREPROCESS_SECONDS, stage))


def source_layout(width, height, bytes_per_line=None):
    w = integer(width, "source_width", 1)
    h = integer(height, "source_height", 1)
    if w * h > MAX_SOURCE_PIXELS:
        raise PlacementError("源图总像素预算超限: {0} > {1}".format(w * h, MAX_SOURCE_PIXELS))
    bpl = w * 4 if bytes_per_line is None else bytes_per_line
    if type(bpl) is not int or bpl < w * 4:
        raise PlacementError("QImage bytesPerLine 小于 RGBA 行长度或不是整数")
    # Python integers do not overflow; check BEFORE pointer sizes / allocation.
    if bpl * h > MAX_DECODED_BYTES:
        raise PlacementError("解码字节预算超限: {0} > {1}".format(bpl * h, MAX_DECODED_BYTES))
    return w, h, bpl


def resource_estimate(sw, sh, w, h, bytes_per_line=None):
    sw, sh, bpl = source_layout(sw, sh, bytes_per_line)
    w, h = integer(w, "output_width", 1), integer(h, "output_height", 1)
    if w > sw or h > sh:
        raise PlacementError("不支持放大图像")
    samples = w * h
    if samples > MAX_OUTPUT_PIXELS:
        raise PlacementError("输出采样工作预算超限: {0} > {1}".format(samples, MAX_OUTPUT_PIXELS))
    # Conservative owned-buffer estimate: encoded input + sanitized snapshot,
    # decoder + converted QImage, bytearray + immutable output, x-index objects.
    # Qt/Krita internals and interpreter/allocator overhead are NOT bounded RSS.
    estimated = 2 * MAX_PNG_BYTES + 2 * bpl * sh + 2 * samples * 4 + w * 40 + 65536
    if estimated > MAX_WORKING_BYTES:
        raise PlacementError("估算工作字节预算超限: {0} > {1}".format(estimated, MAX_WORKING_BYTES))
    return {"source_pixels": sw * sh, "decoded_rgba_bytes": bpl * sh,
            "output_samples": samples, "output_bytes": samples * 4,
            "estimated_working_bytes": estimated}


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
    sw, sh, _ = source_layout(source_width, source_height)
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
    resources = resource_estimate(sw, sh, w, h)
    return {
        "version": 1, "mode": mode, "resources": resources,
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


def _png_header(header):
    if (len(header) != 33 or header[:8] != PNG_SIGNATURE or
            header[8:16] != b"\x00\x00\x00\rIHDR" or
            zlib.crc32(header[12:29]) != struct.unpack(">I", header[29:33])[0]):
        raise PlacementError("PNG 头无效、截断或 IHDR CRC 错误")
    w, h, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", header[16:29])
    legal = {0: (1, 2, 4, 8), 2: (8,), 3: (1, 2, 4, 8), 4: (8,), 6: (8,)}
    if (colour not in legal or depth not in legal[colour] or
            compression != 0 or filtering != 0 or interlace != 0):
        raise PlacementError("仅支持非交错、至多 8bit 的标准静态 PNG（灰度/RGB/索引/RGBA）")
    source_layout(w, h)
    return w, h, depth, colour


def _png_snapshot(encoded, budget):
    """Validate bounded chunks/CRCs, strip non-pixel ancillary metadata.

    Qt never receives compressed text/ICC/unknown ancillary payloads to inflate.
    Pixel-bearing tRNS and fixed-size colour hints are preserved, not rewritten.
    """
    _, _, depth, colour = _png_header(encoded[:33])
    view = memoryview(encoded)
    kept = [view[:33]]
    pos, count, palette = 33, 1, None
    seen = {b"IHDR"}
    idat_started = idat_ended = False
    fixed = {b"sRGB": 1, b"gAMA": 4, b"cHRM": 32, b"pHYs": 9}
    while pos < len(encoded):
        budget.check("PNG chunk 预检")
        count += 1
        if count > MAX_PNG_CHUNKS or len(encoded) - pos < 12:
            raise PlacementError("PNG chunk 数量预算超限或 chunk 截断")
        size, kind = struct.unpack(">I4s", view[pos:pos + 8])
        end = pos + 12 + size
        if (end > len(encoded) or size > MAX_PNG_BYTES or
                any(not (65 <= c <= 90 or 97 <= c <= 122) for c in kind) or kind[2] & 32):
            raise PlacementError("PNG chunk 长度/类型无效")
        if zlib.crc32(view[pos + 4:end - 4]) != struct.unpack(">I", view[end - 4:end])[0]:
            raise PlacementError("PNG chunk CRC 错误")
        if kind in (b"acTL", b"fcTL", b"fdAT"):
            raise PlacementError("不支持动画 PNG")
        retain = False
        if kind == b"IDAT":
            if idat_ended or (colour == 3 and palette is None):
                raise PlacementError("PNG IDAT 顺序或调色板无效")
            idat_started = retain = True
        else:
            idat_ended = idat_started
            if kind == b"IEND":
                if size or not idat_started or end != len(encoded):
                    raise PlacementError("PNG IEND 无效或存在尾随数据")
                kept.append(view[pos:end])
                budget.check("PNG 快照打包前")
                result = b"".join(kept)
                budget.check("PNG 快照打包后")
                return result
            if kind in seen:
                raise PlacementError("PNG chunk 重复")
            if kind == b"PLTE":
                if (idat_started or colour in (0, 4) or size == 0 or size % 3 or
                        size > 768 or (colour == 3 and size // 3 > 2 ** depth)):
                    raise PlacementError("PNG PLTE 无效")
                palette = size // 3
                retain = True
            elif kind == b"tRNS":
                valid = ((colour == 0 and size == 2) or (colour == 2 and size == 6) or
                         (colour == 3 and palette is not None and 0 < size <= palette))
                if idat_started or not valid:
                    raise PlacementError("PNG tRNS 无效")
                retain = True
            elif kind in fixed:
                if idat_started or size != fixed[kind]:
                    raise PlacementError("PNG 固定元数据长度/顺序无效")
                retain = True
            elif not kind[0] & 32:
                raise PlacementError("PNG 不支持的关键 chunk")
            # Unknown ancillary/text/ICC are deliberately not passed to Qt.
            # Repeated text chunks are legal, so only retained chunks are unique.
            if retain:
                seen.add(kind)
        if retain:
            kept.append(view[pos:end])
        pos = end
    raise PlacementError("PNG 缺少 IEND")


def _fs_bytes(path):
    """Explicit UTF-8 bytes for OS calls: Krita's embedded Python may run with
    a non-UTF-8 filesystem encoding (live-verified 2026-10: str paths with any
    non-ASCII byte fail with ENOENT even when the file exists). Bytes paths
    bypass the codec entirely."""
    return path.encode("utf-8") if isinstance(path, str) else path


def prepare_png(path, canvas_width, canvas_height, mode=None, offset_x=0,
                offset_y=0, fit_canvas=None, *, budget=None, expected_source_sha256=None):
    """Read one bounded regular-file snapshot; no path-based Qt second read."""
    budget = budget or PreprocessBudget()
    budget.check("PNG 打开前")
    if "\x00" in path:
        raise PlacementError("PNG 路径含空字符")
    try:
        # O_NONBLOCK avoids waiting on FIFOs; fstat rejects all nonregular files.
        fd = os.open(_fs_bytes(path), os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 33 <= before.st_size <= MAX_PNG_BYTES:
                raise PlacementError("PNG 必须是 33 字节..32MiB 的普通文件（文件预算）")
            header = stream.read(33)
            sw, sh, _, _ = _png_header(header)
            mapping = plan_placement(sw, sh, canvas_width, canvas_height,
                                     mode, offset_x, offset_y, fit_canvas)
            budget.check("PNG 文件读取前")
            stream.seek(0)
            encoded = stream.read(MAX_PNG_BYTES + 1)
            after = os.fstat(stream.fileno())
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if (identity(before) != identity(after) or len(encoded) != before.st_size or
                    encoded[:33] != header):
                raise PlacementError("PNG 文件在预检读取期间发生变化")
        budget.check("PNG 文件读取后")
        source_sha256 = hashlib.sha256(encoded).hexdigest()
        if expected_source_sha256 is not None and source_sha256 != expected_source_sha256:
            raise PlacementError("PNG 来源摘要改变；拒绝创建图层")
        mapping["source_sha256"] = source_sha256
        return _png_snapshot(encoded, budget), mapping
    except OSError as exc:
        raise PlacementError("PNG 读取失败: {0}: {1}".format(path, exc.strerror or exc)) from exc


def packed_rgba(raw, width, height, bytes_per_line, *, budget=None):
    """Strip row padding once. Production resize samples strided input directly."""
    budget = budget or PreprocessBudget()
    budget.check("去行填充前")
    w, h, bpl = source_layout(width, height, bytes_per_line)
    if len(raw) < bpl * h:
        raise PlacementError("QImage 像素缓冲区长度不足")
    if bpl == w * 4:
        if isinstance(raw, bytes) and len(raw) == bpl * h:
            return raw
        try:
            view = memoryview(raw).cast("B")
        except TypeError:
            result = bytes(raw[:w * h * 4])  # legacy sliceable buffer fallback
        else:
            result = view[:w * h * 4].tobytes()  # one immutable copy, not bytearray + bytes
        budget.check("连续像素打包后")
        return result
    out = bytearray(w * h * 4)
    for r in range(h):
        budget.check("去行填充")
        out[r * w * 4:(r + 1) * w * 4] = raw[r * bpl:r * bpl + w * 4]
    result = bytes(out)
    budget.check("去行填充后")
    return result


def resize_rgba(data, plan, *, bytes_per_line=None, budget=None):
    """Nearest pixel-center sampling directly from the QImage strided buffer.

    No whole-source packing for downscales. Channels/hidden RGB are unchanged.
    """
    budget = budget or PreprocessBudget()
    budget.check("采样前")
    sw, sh = plan["source_size"]
    w, h = plan["output_size"]
    resource_estimate(sw, sh, w, h, bytes_per_line)
    sw, sh, bpl = source_layout(sw, sh, bytes_per_line)
    if len(data) != bpl * sh:
        raise PlacementError("RGBA 像素缓冲区长度不符")
    if (sw, sh) == (w, h):
        return packed_rgba(data, sw, sh, bpl, budget=budget)
    xs = [((2 * u + 1) * sw // (2 * w)) * 4 for u in range(w)]
    budget.check("输出分配前")
    out = bytearray(w * h * 4)
    for v in range(h):
        budget.check("最近邻采样")
        row = ((2 * v + 1) * sh // (2 * h)) * bpl
        dst = v * w * 4
        for u, sx in enumerate(xs):
            start = dst + u * 4
            out[start:start + 4] = data[row + sx:row + sx + 4]
    budget.check("输出打包前")
    result = bytes(out)
    budget.check("采样后")
    return result
