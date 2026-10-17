"""CAD-13: main-thread, full-canvas projection/selection snapshots.

No Krita/Qt imports: the byte codec and filesystem contract are offline-testable.
The caller MUST run export() in the existing MainThreadBridge callback. This is
not a lock against Krita worker threads; waitForDone/refreshProjection fence the
projection before sampling. No event loop is pumped between the two reads.
"""
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import time
from datetime import datetime, timezone
import uuid
import zlib


class SnapshotError(ValueError):
    """Invalid request, unsupported API/format, or incomplete output."""


# Only a known sRGB TRC is supported: silently dropping a wide-gamut/linear ICC
# profile would change the meaning of the PNG samples. No implicit conversion.
SUPPORTED_PROFILE = "sRGB-elle-V2-srgbtrc.icc"


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _size(width, height):
    if (type(width) is not int or type(height) is not int or
            not 1 <= width <= 32768 or not 1 <= height <= 32768):
        raise SnapshotError("无效画布尺寸")


def _pixels(data, width, height, channels):
    _size(width, height)
    if isinstance(data, (int, str)):
        raise SnapshotError("pixelData 必须是原生字节缓冲区")
    try:
        raw = bytes(data)
    except (TypeError, ValueError) as exc:
        raise SnapshotError("pixelData 不是可读取的字节缓冲区") from exc
    expected = width * height * channels
    if len(raw) != expected:
        raise SnapshotError("pixelData 长度异常: {0} != {1}".format(len(raw), expected))
    return raw


# Live-verified 2026-10 (Krita 6.0.4 AppImage): Document.pixelData and
# Node.pixelData both return native RGBA order. An earlier B/R swap here
# traded red and blue in every projection export; it must not come back.
MAGENTA = (255, 0, 255)


def alpha_summary(data, width, height):
    """Nonzero-alpha coverage and tight bbox, in bounds-local coordinates."""
    raw = _pixels(data, width, height, 4)
    alpha = raw[3::4]
    return {"coverage_pixels": len(alpha) - alpha.count(0),
            "bbox": mask_bounds(alpha, width, height)}


_BLEND_LUTS = None


def _blend_luts():
    global _BLEND_LUTS
    if _BLEND_LUTS is None:
        _BLEND_LUTS = tuple(
            bytes((v * a + c * (255 - a) + 127) // 255
                  for a in range(256) for v in range(256))
            for c in MAGENTA)
    return _BLEND_LUTS


def flatten_over_magenta(data, width, height):
    """Straight-alpha RGBA over opaque magenta; agent vision has no reliable
    alpha rendering, so transparency is drawn explicitly. Returns
    (opaque_rgba, near_magenta_pixels): content pixels near magenta are
    counted, not hidden — the caller reports the residual ambiguity."""
    raw = _pixels(data, width, height, 4)
    lut_r, lut_g, lut_b = _blend_luts()
    mr, mg, mb = MAGENTA
    out = bytearray(len(raw))
    near = 0
    for i in range(0, len(raw), 4):
        a = raw[i + 3]
        if a == 0:
            out[i], out[i + 1], out[i + 2], out[i + 3] = mr, mg, mb, 255
            continue
        if a == 255:
            r, g, b = raw[i], raw[i + 1], raw[i + 2]
        else:
            base = a << 8
            r = lut_r[base | raw[i]]
            g = lut_g[base | raw[i + 1]]
            b = lut_b[base | raw[i + 2]]
        out[i], out[i + 1], out[i + 2], out[i + 3] = r, g, b, 255
        if r >= 192 and g <= 63 and b >= 192:
            near += 1
    return bytes(out), near


def _chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload +
            struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))


def encode_png(data, width, height, channels):
    """Lossless 8-bit RGBA (6) or grayscale (0); gray has NO alpha channel."""
    if channels not in (1, 4):
        raise SnapshotError("PNG 仅支持 RGBA8 或 Gray8")
    raw = _pixels(data, width, height, channels)
    stride = width * channels
    scanlines = b"".join(b"\0" + raw[y * stride:(y + 1) * stride]
                         for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6 if channels == 4 else 0, 0, 0, 0)
    # No sRGB/gAMA/iCCP chunk on either variant: samples are gated to sRGB
    # (SUPPORTED_PROFILE) and untagged PNG reads as sRGB by convention; the
    # explicit sRGB chunk renders as a black frame in some agent vision
    # decoders. Gray8 is coverage, NOT color, and must stay untagged anyway.
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) +
            _chunk(b"IDAT", zlib.compress(scanlines, 6)) + _chunk(b"IEND", b""))


def mask_bounds(data, width, height):
    """Nonzero coverage bounds clipped to canvas; preserve every feather value."""
    raw = _pixels(data, width, height, 1)
    left, top, right, bottom = width, height, -1, -1
    for y in range(height):
        row = raw[y * width:(y + 1) * width]
        if not any(row):
            continue
        xs = [x for x, value in enumerate(row) if value]
        left, right = min(left, xs[0]), max(right, xs[-1])
        top, bottom = min(top, y), y
    if right < 0:
        return None
    return {"x": left, "y": top, "width": right - left + 1, "height": bottom - top + 1}


def _root_uuid(doc):
    try:
        value = uuid.UUID(str(doc.rootNode().uniqueId().toString()).strip("{}"))
    except (AttributeError, ValueError, TypeError) as exc:
        raise SnapshotError("无法获取 rootNode 原生 UUID; 拒绝以 wrapper/文件名替代身份") from exc
    if not value.int:
        raise SnapshotError("rootNode UUID 为空")
    return str(value)


class DocumentTokens:
    """Conservative session identity, NOT a persisted document identifier.

    Any imageCreated/imageClosed invalidates ALL tokens, including other open
    documents. This avoids matching a reopened .kra whose root UUID is retained.
    Missing/disabled notifier fails closed. Actual Krita signal delivery remains
    a real-runtime acceptance gate; offline signal doubles cannot establish it.
    """
    def __init__(self, app):
        self.app = app
        self.epoch = uuid.uuid4().hex
        self.tokens = {}
        try:
            self.notifier = app.notifier()
            self.notifier.imageClosed.connect(self.invalidate)
            self.notifier.imageCreated.connect(self.invalidate)
            self.notifier.setActive(True)
            self.check_active()
        except (AttributeError, RuntimeError, TypeError) as exc:
            self.invalidate()
            raise SnapshotError("缺少可用的 Krita 生命周期 notifier; 无法建立文档 token") from exc

    def invalidate(self, *args):
        self.epoch = uuid.uuid4().hex
        self.tokens.clear()

    def check_active(self):
        if not self.notifier.active():
            self.invalidate()
            raise SnapshotError("Krita notifier 已停用; 旧 token 已失效")

    def token(self, doc, expected=None):
        self.check_active()
        root_uuid = _root_uuid(doc)
        # Do not use Python object identity: Krita can return fresh wrappers.
        ids = [_root_uuid(d) for d in self.app.documents()]
        if ids.count(root_uuid) != 1:
            self.invalidate()
            raise SnapshotError("文档 root UUID 缺失或多个打开文档 UUID 冲突")
        # Drop tokens for documents no longer present even if a signal was missed.
        self.tokens = {key: value for key, value in self.tokens.items() if key in ids}
        token = self.tokens.setdefault(root_uuid, "doc-" + uuid.uuid4().hex)
        if expected is not None and (not isinstance(expected, str) or expected != token):
            raise SnapshotError("document token 已失效或不属于当前文档")
        return token


def _output_paths(paths, overwrite):
    if type(overwrite) is not bool:
        raise SnapshotError("overwrite 必须是布尔值")
    result = {}
    for kind, value in paths.items():
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            raise SnapshotError("输出路径必须是非空字符串")
        path = Path(value).expanduser()
        if path.is_symlink():
            raise SnapshotError("拒绝覆盖符号链接: {0}".format(path))
        path = path.resolve()
        if path.suffix.lower() != ".png" or not path.parent.is_dir():
            raise SnapshotError("输出须为 .png 且父目录必须已存在: {0}".format(path))
        if path.exists() and (not overwrite or not path.is_file()):
            raise SnapshotError("输出已存在或不是普通文件; 覆盖须显式 overwrite=true: {0}".format(path))
        for previous in result.values():
            if path == previous or (path.exists() and previous.exists() and os.path.samefile(path, previous)):
                raise SnapshotError("投影与选区输出必须是不同文件")
        result[kind] = path
    return result


def publish_pngs(outputs, overwrite):
    """Stage ALL PNGs before publishing; each filename atomic, pair is NOT atomic.

    No-clobber uses a same-filesystem hard link, overwrite uses os.replace. On
    partial publish, report the already-published paths, never claim success or
    delete/restore a caller's file. A crash/HTTP timeout may leave outputs too.
    """
    staged, published = [], []
    try:
        for path, png in outputs:
            fd, name = tempfile.mkstemp(prefix=".cwb-snapshot-", suffix=".png", dir=str(path.parent))
            temporary = Path(name).resolve()
            staged.append(temporary)
            with os.fdopen(fd, "wb") as stream:
                if stream.write(png) != len(png):
                    raise OSError("PNG 短写")
                stream.flush()
                os.fsync(stream.fileno())
        for (path, _), temporary in zip(outputs, staged):
            # Both paths were resolved/validated above; only our generated temp
            # files are cleaned up. Never unlink an output path on failure.
            if overwrite:
                os.replace(temporary, path)
            else:
                os.link(temporary, path)
            published.append(str(path))
    except OSError as exc:
        raise SnapshotError("PNG 发布失败; published_paths={0}; {1}".format(published, exc)) from exc
    finally:
        for temporary in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Failure must not turn a partial publish into success.


class SnapshotService:
    def __init__(self, app):
        self.app = app
        self.identities = DocumentTokens(app)

    def export(self, *, projection_path=None, selection_path=None, overwrite=False,
               selection_mode="selection", expected_document_token=None):
        """One invocation == one main-thread callback, even for a paired export."""
        if selection_mode not in ("selection", "full_canvas"):
            raise SnapshotError("selection_mode 仅支持 selection/full_canvas")
        requested = {}
        if projection_path is not None:
            requested["projection"] = projection_path
        if selection_path is not None:
            requested["selection"] = selection_path
        if not requested:
            raise SnapshotError("必须指定输出路径")
        paths = _output_paths(requested, overwrite)
        doc = self.app.activeDocument()
        if doc is None:
            raise SnapshotError("没有打开的文档")
        required_api = ("rootNode", "width", "height", "colorProfile", "waitForDone",
                        "refreshProjection", "pixelData", "selection")
        if any(not callable(getattr(doc, name, None)) for name in required_api):
            raise SnapshotError("不支持的 Document API; 需要原生投影/选区及 waitForDone 接口")
        root = doc.rootNode()
        if root.colorModel() != "RGBA" or root.colorDepth() != "U8":
            raise SnapshotError("仅支持 RGBA/U8 文档")
        if doc.colorProfile() != SUPPORTED_PROFILE:
            raise SnapshotError("不支持的色彩配置; 仅支持 {0}".format(SUPPORTED_PROFILE))
        width, height = doc.width(), doc.height()
        _size(width, height)
        token = self.identities.token(doc, expected_document_token)
        epoch = self.identities.epoch
        # No document/node/selection setters, save, or event-loop pumping.
        doc.waitForDone()
        doc.refreshProjection()
        doc.waitForDone()
        started_ns = time.monotonic_ns()
        sampled_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        captured, details = {}, {}
        if "projection" in paths:
            raw = _pixels(doc.pixelData(0, 0, width, height), width, height, 4)
            captured["projection"] = raw
            details["projection"] = {"source": "Document.pixelData", "source_layout": "RGBA8",
                                     "source_sha256": _digest(raw), "pixel_layout": "RGBA8",
                                     "alpha": "straight; preserved", "color_profile": SUPPORTED_PROFILE}
        if "selection" in paths:
            if selection_mode == "full_canvas":
                mask = b"\xff" * (width * height)
                native_bounds = None
            else:
                selection = doc.selection()
                if selection is None:
                    raise SnapshotError("没有选区; 可显式指定 selection_mode=full_canvas")
                if any(not callable(getattr(selection, name, None))
                       for name in ("pixelData", "x", "y", "width", "height")):
                    raise SnapshotError("不支持的 Selection 原生单通道 API")
                mask = _pixels(selection.pixelData(0, 0, width, height), width, height, 1)
                native_bounds = {"x": selection.x(), "y": selection.y(),
                                 "width": selection.width(), "height": selection.height()}
            bounds = mask_bounds(mask, width, height)
            if bounds is None:
                raise SnapshotError("选区在画布内没有非零覆盖像素")
            captured["selection"] = mask
            details["selection"] = {"source": "Selection.pixelData" if selection_mode == "selection" else "explicit_full_canvas",
                                    "pixel_layout": "Gray8", "alpha": "none",
                                    "coverage": "0=unselected; 255=fully selected; 1..254=feather",
                                    "mode": selection_mode, "bounds": bounds,
                                    "native_bounds": native_bounds}
        ended_ns = time.monotonic_ns()
        # If waitForDone caused lifecycle activity, refuse to publish mixed data.
        if self.identities.token(doc, token) != token or self.identities.epoch != epoch:
            raise SnapshotError("采样期间文档生命周期变化; 快照已丢弃")
        if doc.width() != width or doc.height() != height:
            raise SnapshotError("采样期间画布尺寸变化; 快照已丢弃")
        outputs = []
        for kind, raw in captured.items():
            png = encode_png(raw, width, height, 4 if kind == "projection" else 1)
            details[kind].update(pixel_sha256=_digest(raw), pixel_bytes=len(raw),
                                 path=str(paths[kind]), file_bytes=len(png), file_sha256=_digest(png))
            outputs.append((paths[kind], png))
        publish_pngs(outputs, overwrite)
        paired = len(captured) == 2
        return {"capture_id": "capture-" + uuid.uuid4().hex, "document_token": token,
                "token_contract": "session/notifier-epoch/root-uuid-v1",
                "token_lifecycle_runtime_verified": False,
                "paired": paired, "capture_scope": "one-main-thread-callback" if paired else "single-export-only",
                "width": width, "height": height, "origin": {"x": 0, "y": 0},
                "sampled_at": sampled_at, "sample_started_monotonic_ns": started_ns,
                "sample_finished_monotonic_ns": ended_ns,
                "projection_refresh": "waitForDone/refreshProjection/waitForDone", **details}


_service = None


def export_snapshot(app, **kwargs):
    """Lazy process-session service; invoked ONLY inside the ops bridge callback."""
    global _service
    if _service is None:
        _service = SnapshotService(app)
    return _service.export(**kwargs)
