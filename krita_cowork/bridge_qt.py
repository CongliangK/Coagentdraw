"""MainThreadBridge:把任意可调用对象投递到 Qt 主线程执行并同步取回结果。

Krita 的脚本 API 只允许在主线程调用;HTTP 工作线程通过 call() 转发。
超时后置 cancelled 标志,主线程迟到执行会被跳过,避免客户端重试导致重复副作用。
"""
import threading

try:  # Krita 6 AppImage 内为 PyQt6;兼容老版 Krita 的 PyQt5
    from PyQt6.QtCore import QObject, pyqtSignal
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, pyqtSignal


class _Runner(QObject):
    run_signal = pyqtSignal(object, object)  # (fn, result_box)

    def __init__(self):
        super().__init__()
        self.run_signal.connect(self._on_run)

    def _on_run(self, fn, box):
        if box.get("cancelled"):
            return  # 调用方已超时放弃,跳过迟到执行
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - 异常需原样带回调用线程
            box["error"] = exc
        finally:
            box["event"].set()


class MainThreadBridge(object):
    """必须在 Qt 主线程构造一次,随插件存活;不主动断开连接。"""

    def __init__(self):
        self._runner = _Runner()

    def call(self, fn, timeout=10.0):
        box = {"event": threading.Event()}
        self._runner.run_signal.emit(fn, box)
        if not box["event"].wait(timeout):
            box["cancelled"] = True
            raise TimeoutError("Krita 主线程忙(笔刷/模态框?),{}s 内未响应".format(timeout))
        if "error" in box:
            raise box["error"]  # 原样重抛,保留异常类型供 HTTP 层分类
        return box.get("result")
