"""嵌入式包裝 — 讓 XML 批次轉換工具 跑在 Launcher 的分頁裡。

實作 create_frame(parent) -> ttk.Frame,由 Launcher 動態載入。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk

_TOOL_ROOT = Path(__file__).parent


def _load_converttool():
    """用 importlib 從絕對路徑載入 converttool.py,給唯一模組名避免衝突。"""
    spec = importlib.util.spec_from_file_location(
        "_xbc_converttool", _TOOL_ROOT / "converttool.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_xbc_converttool"] = mod
    spec.loader.exec_module(mod)
    return mod


_tool = _load_converttool()
_App = _tool.App


class _EmbeddedApp(_App):
    """把 App 嵌進任意 Tkinter widget(不需要 tk.Tk)。"""

    def __init__(self, parent: tk.Widget) -> None:
        self.root = parent
        # config.json 放使用者家目錄,工具更新(重裝)時不會被清掉
        self.config_path = Path.home() / ".xml_batch_convert_config.json"
        self.config = None
        self.config_error = None

        self._setup_style()
        self._build_ui()
        self._load_config(initial=True)

    def _setup_style(self) -> None:
        """嵌入模式:只定義工具需要的 Accent.TButton,不動全域主題與字型。"""
        style = ttk.Style()
        try:
            style.configure(
                "Accent.TButton", padding=[20, 6],
                font=("Microsoft JhengHei UI", 10, "bold"),
            )
        except tk.TclError:
            pass


def create_frame(parent: tk.Widget) -> ttk.Frame:
    frame = ttk.Frame(parent)
    _EmbeddedApp(frame)
    return frame
