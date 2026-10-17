"""ControlServer:Krita 进程内的 HTTP JSON 控制面(仅监听 127.0.0.1)。

规避条款落实:
- ThreadingHTTPServer + daemon_threads + block_on_close=False(退出不悬挂);
- HTTP/1.1 + Content-Length;chunked/坏 Content-Length/坏 JSON → 4xx 并关连接
  (防半包污染 keep-alive 下一请求);
- 必填参数在路由层校验(400),不落入主线程;
- Krita 调用全部经 MainThreadBridge,超时 10s → 503;
- OpError→400,NotFoundError→404,其余异常→500 带短堆栈。
"""
import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ops
from .bridge_qt import MainThreadBridge

# POST 路由:路径 → (函数, 必填参数, 可选参数);ALIAS 把协议参数名映射为函数形参
POST_ROUTES = {
    "/write/v1/receipt": (ops.write_receipt, ("operation_id", "service_id"), ()),
    "/write/v1/group": (ops.guarded_group,
                        ("name", "operation_id", "expected_target", "expected_bindings"),
                        ("parent_id", "above")),
    "/write/v1/add": (ops.guarded_add,
                      ("png_path", "name", "operation_id", "expected_target", "expected_bindings", "source_sha256"),
                      ("above", "offset_x", "offset_y", "parent_id", "mode", "fit_canvas")),
    "/doc/new": (ops.doc_new, ("name", "width", "height"), ()),
    "/doc/save": (ops.doc_save_as, ("path",), ()),
    "/doc/export": (ops.doc_export, ("path",), ("overwrite", "expected_document_token")),
    "/doc/selection_png": (ops.doc_selection_png, ("path",),
                           ("selection_mode", "overwrite", "expected_document_token")),
    "/doc/snapshot": (ops.doc_snapshot, ("projection_path", "selection_path"),
                      ("selection_mode", "overwrite", "expected_document_token")),
    "/layer/png": (ops.layer_png, ("id", "path"), ("underlay",)),
    "/layer/group": (ops.layer_group, ("name",), ("parent_id", "above")),
    "/layer/add": (ops.layer_add, ("png_path", "name"),
                   ("above", "offset_x", "offset_y", "parent_id", "mode", "fit_canvas")),
    "/layer/vector": (ops.layer_vector,
                      ("svg", "name", "target_document", "canvas", "dpi", "dedicated_document"),
                      ("parent_id", "above")),
    "/layer/props": (ops.layer_props, ("id",), ("alpha_locked", "opacity", "blending_mode")),
    "/layer/visible": (ops.layer_set_visible, ("id", "visible"), ()),
    "/layer/rename": (ops.layer_rename, ("id", "name"), ()),
}
GET_ROUTES = {
    "/write/v1/target": ops.write_target,
    "/ping": ops.ping,
    "/docs": ops.doc_info,
    "/layers": ops.layers,
}
ALIAS = {"id": "layer_id", "above": "above_layer_id"}


class ControlServer(object):
    def __init__(self, port):
        self.port = port
        self.bridge = MainThreadBridge()
        self._httpd = None

    @property
    def running(self):
        return self._httpd is not None

    def start(self):
        if self.running:
            return
        bridge = self.bridge

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass

            def _reply(self, code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                if self.close_connection:
                    self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._dispatch("GET")

            def do_POST(self):
                self._dispatch("POST")

            def _dispatch(self, method):
                try:
                    fn, kwargs = self._route(method)
                    result = bridge.call(lambda: fn(**kwargs))
                    self._reply(200, result)
                except TimeoutError as exc:
                    self._reply(503, {"error": str(exc)})
                except ops.NotFoundError as exc:
                    self._reply(404, {"error": str(exc)})
                except ops.PropsWriteError as exc:
                    self._reply(400, exc.payload)
                except ops.OpError as exc:
                    self._reply(400, {"error": str(exc)})
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True
                except Exception as exc:  # noqa: BLE001
                    self._reply(500, {
                        "error": "{0}: {1}".format(type(exc).__name__, exc),
                        "where": traceback.format_exc(limit=3),
                    })

            def _read_body(self):
                """读 JSON body;不可信输入先关连接再抛 OpError(→400)。"""
                if self.headers.get("Transfer-Encoding"):
                    self.close_connection = True
                    raise ops.OpError("不支持 chunked 请求体,请带 Content-Length")
                raw = self.headers.get("Content-Length")
                try:
                    length = int(raw) if raw else 0
                except ValueError:
                    self.close_connection = True
                    raise ops.OpError("Content-Length 非数字")
                if self.path.startswith("/write/v1/") and not 1 <= length <= 65536:
                    self.close_connection = True
                    raise ops.OpError("write/v1 JSON body 必须为 1..64KiB")
                if self.path.split("?")[0].rstrip("/") == "/layer/vector" and not 1 <= length <= 131072:
                    self.close_connection = True
                    raise ops.OpError("vector JSON body 必须为 1..128KiB")
                try:
                    data = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    self.close_connection = True
                    raise ops.OpError("JSON 解析失败")
                if not isinstance(data, dict):
                    self.close_connection = True
                    raise ops.OpError("body 必须是 JSON 对象")
                return data

            def _route(self, method):
                path = self.path.split("?")[0].rstrip("/")
                if method == "GET":
                    if path in GET_ROUTES:
                        return GET_ROUTES[path], {}
                    raise ops.OpError("未知 GET 路径: {0}".format(path))
                if path not in POST_ROUTES:
                    raise ops.OpError("未知 POST 路径: {0}".format(path))
                data = self._read_body()
                fn, required, optional = POST_ROUTES[path]
                missing = [k for k in required if k not in data]
                if missing:
                    raise ops.OpError("缺少必填参数: {0}".format(", ".join(missing)))
                if path in ("/layer/props", "/layer/vector") or path.startswith("/write/v1/"):
                    unknown = set(data) - set(required + optional)
                    if unknown or (path == "/layer/props" and not any(k in data for k in optional)):
                        raise ops.OpError("需要已知字段/属性,不接受未知字段")
                kwargs = {ALIAS.get(k, k): data[k]
                          for k in required + optional if k in data}
                return fn, kwargs

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False
            allow_reuse_address = True

        self._httpd = Server(("127.0.0.1", self.port), Handler)
        import threading
        t = threading.Thread(target=self._httpd.serve_forever,
                             kwargs={"poll_interval": 0.2}, daemon=True)
        t.start()

    def stop(self):
        # 不 disconnect 信号:bridge 与插件同生命周期,断开会造成重开失效
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
