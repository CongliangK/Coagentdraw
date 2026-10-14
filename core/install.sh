#!/usr/bin/env bash
# 安装/更新 krita_cowork 插件到本机 Krita(6.0.4 AppImage 已验证格式)。
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
PYKRITA="$HOME/.local/share/krita/pykrita"
KRITARC="$HOME/.config/kritarc"

if pgrep -x krita >/dev/null 2>&1; then
  echo "⚠️  Krita 正在运行:退出时会用内存态覆写 kritrc,使本脚本的启用配置丢失。" >&2
  echo "    请先关闭 Krita 再运行本脚本(仅更新代码可忽略此警告)。" >&2
fi

mkdir -p "$PYKRITA"
rm -rf "$PYKRITA/krita_cowork"
cp -r "$SRC/krita_cowork" "$PYKRITA/krita_cowork"
rm -rf "$PYKRITA/krita_cowork/__pycache__" 2>/dev/null || true
find "$PYKRITA/krita_cowork" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
cp "$SRC/kritapykrita_krita_cowork.desktop" "$PYKRITA/"

# 在 kritrc 组 [python] 写 enable_krita_cowork=true(键名来自 Krita 源码
# PythonPluginManager.cpp:KConfigGroup(openConfig(),"python")/enable_<module>)
# 用 awk 按组定位,避免全文 grep 误匹配与多组头重复插入。
touch "$KRITARC"
awk -v key="enable_krita_cowork=true" '
  BEGIN { in_group=0; done=0 }
  /^\[python\][ \t]*$/ { in_group=1; print; next }
  /^\[/ { if (in_group && !done) { print key; done=1 } in_group=0; print; next }
  in_group && /^enable_krita_cowork=/ { if (!done) { print key; done=1 } next }
  { print }
  END { if (!done) { if (!in_group) print "[python]"; print key } }
' "$KRITARC" > "$KRITARC.tmp" && mv "$KRITARC.tmp" "$KRITARC"

chmod +x "$SRC/cwb.py" 2>/dev/null || true
echo "已安装到 $PYKRITA(krita_cowork,[python] enable=true)。重启 Krita 生效。"
