# krita_cowork - Krita × ComfyUI × 智能体 复合工作台插件
# Krita 加载本模块时注册扩展;扩展在首个窗口创建时启动控制服务。
from krita import Krita

from .plugin import CoworkPlugin

Krita.instance().addExtension(CoworkPlugin(Krita.instance()))
