"""CoworkPlugin:负责生命周期——Krita 启动时开启控制服务,退出时关闭。"""
from krita import Krita, Extension

from .server import ControlServer

PORT = 51080  # 本机回环专用,避开设常用端口


class CoworkPlugin(Extension):
    def __init__(self, parent):
        super(CoworkPlugin, self).__init__(parent)
        self.server = None
        self._started = False

    def setup(self):
        pass

    def createActions(self, window):
        # 首个窗口创建时自动启动服务(保证处于主线程且事件循环已就绪)。
        if not self._started:
            self._started = True
            self._start()
        action = window.createAction("cowork_status", "Cowork: 开关控制服务")
        action.triggered.connect(self._toggle)

    def _toggle(self):
        if self.server is None or not self.server.running:
            self._start()
        else:
            self._stop()

    def _start(self):
        if self.server is None:
            self.server = ControlServer(PORT)
        self.server.start()

    def _stop(self):
        if self.server is not None:
            self.server.stop()
