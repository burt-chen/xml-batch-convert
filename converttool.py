#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XML 批次轉換工具 (GUI 版)

依 CSV 對應表,套用範例 XML 模板,批次產生多個 XML 檔。
詳細說明見 README.md。
"""

import csv
import json
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

CONFIG_FILE = "config.json"


def app_dir():
    """程式所在資料夾。PyInstaller 打包後也能正確指向 .exe 旁邊。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

# 特殊規則類型:內部 key ↔ 中文顯示名稱
RULE_TYPE_LABELS = {
    "clear_lines_if_column_empty": "欄位為空時刪除指定行",
}


def rule_type_label(t):
    return RULE_TYPE_LABELS.get(t, t)


def rule_type_key(label):
    for k, v in RULE_TYPE_LABELS.items():
        if v == label:
            return k
    return label


# 內建預設值:空白骨架。首次啟動 / 重設時為空,讓使用者自行建立或匯入範例。
DEFAULT_CONFIG = {
    "filename_template": "",
    "min_required_columns": 1,
    "mappings": [],
    "special_rules": [],
}

# 完整範例設定:供「下載範例設定」按鈕輸出,讓使用者有可直接套用的參考。
SAMPLE_CONFIG = {
    "filename_template": "{C}",
    "min_required_columns": 1,
    "mappings": [
        {"column": "A", "line": 5,   "tag": "gco:CharacterString", "note": "A 欄"},
        {"column": "B", "line": 181, "tag": "gco:CharacterString", "note": "B 欄"},
        {"column": "C", "line": 366, "tag": "gco:CharacterString", "note": "C 欄 (圖號)"},
        {"column": "D", "line": 372, "tag": "gco:CharacterString", "note": "D 欄"},
        {"column": "E", "line": 375, "tag": "gco:CharacterString", "note": "E 欄"},
        {"column": "F", "line": 448, "tag": "gco:Decimal",         "note": "F 欄"},
        {"column": "G", "line": 451, "tag": "gco:Decimal",         "note": "G 欄"},
        {"column": "H", "line": 454, "tag": "gco:Decimal",         "note": "H 欄"},
        {"column": "I", "line": 457, "tag": "gco:Decimal",         "note": "I 欄"},
    ],
    "special_rules": [
        {
            "name": "E 欄為空時刪除行 374~376",
            "type": "clear_lines_if_column_empty",
            "column": "E",
            "lines": [374, 375, 376],
        }
    ],
}

# 範例 CSV / XML 內容直接內嵌 (見檔尾 SAMPLE_CSV_TEXT / SAMPLE_XML_TEXT),
# 不依賴任何外部實體檔,打包時也不需 .spec datas。
SAMPLE_CSV_NAME = "CSV對應表範例.csv"
SAMPLE_XML_NAME = "範例xml.xml"


# ---------- 工具函式 ----------

def col_to_index(col):
    """欄位 (A/B/C... 或 0/1/2...) → 0-based index"""
    if isinstance(col, bool):
        raise ValueError(f"無法解析欄位: {col!r}")
    if isinstance(col, int):
        return col
    if isinstance(col, str):
        s = col.strip()
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
        if s.isalpha():
            idx = 0
            for ch in s.upper():
                idx = idx * 26 + (ord(ch) - ord("A") + 1)
            return idx - 1
    raise ValueError(f"無法解析欄位: {col!r}")


def index_to_letter(idx):
    """0-based index → 字母 (A/B/C...)"""
    if idx < 0:
        return str(idx)
    s = ""
    n = idx + 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def col_display(col):
    """設定中欄位的顯示文字 (A/B/C 形式)"""
    try:
        return index_to_letter(col_to_index(col))
    except Exception:
        return str(col)


def get_indent(line):
    out = []
    for ch in line:
        if ch in (" ", "\t"):
            out.append(ch)
        else:
            break
    return "".join(out)


SAFE_FN_RE = re.compile(r'[\\/:*?"<>|\r\n]+')

def safe_filename(name):
    return SAFE_FN_RE.sub("_", name).strip() or "_"


PLACEHOLDER_RE = re.compile(r"\{([A-Za-z]+|\d+)\}")

def build_filename(template, row, fallback_key=""):
    """以 template 套上 row 的值產生檔名 (不含副檔名)。
    template 中 {A}/{B}/{C}.../{0}/{1}... 會被替換成 row 對應欄位值。
    無法解析的占位符保持原樣。空字串會 fallback 為 fallback_key。"""
    def repl(m):
        col = m.group(1)
        try:
            idx = col_to_index(col)
        except ValueError:
            return m.group(0)
        return row[idx] if idx < len(row) else ""
    name = PLACEHOLDER_RE.sub(repl, template or "")
    name = safe_filename(name)
    if name == "_":
        name = safe_filename(fallback_key)
    return name


# ---------- 轉換邏輯 ----------

def apply_mappings(xml_lines, csv_row, mappings):
    msgs = []
    for m in mappings:
        try:
            ci = col_to_index(m["column"])
        except Exception as e:
            msgs.append(f"⚠ mapping 欄位錯誤: {e}")
            continue
        line_no = int(m["line"])
        tag = m["tag"]
        if line_no < 1 or line_no > len(xml_lines):
            msgs.append(f"⚠ 範例 XML 無第 {line_no} 行 (mapping {col_display(m['column'])})")
            continue
        value = csv_row[ci] if ci < len(csv_row) else ""
        indent = get_indent(xml_lines[line_no - 1])
        xml_lines[line_no - 1] = f"{indent}<{tag}>{value}</{tag}>\n"
    return xml_lines, msgs


def apply_special_rules(xml_lines, csv_row, rules):
    """套用特殊規則。會收集所有需刪除的行號 (1-based, 對原始行號),最後一次性
    從 xml_lines 移除,確保多條規則之間互不影響行號計算。"""
    msgs = []
    lines_to_delete = set()
    for r in rules:
        rtype = r.get("type")
        if rtype == "clear_lines_if_column_empty":
            try:
                ci = col_to_index(r["column"])
            except Exception as e:
                msgs.append(f"⚠ rule 欄位錯誤: {e}")
                continue
            value = csv_row[ci] if ci < len(csv_row) else ""
            if value == "":
                for ln in r.get("lines", []):
                    if 1 <= ln <= len(xml_lines):
                        lines_to_delete.add(ln)
        else:
            msgs.append(f"⚠ 未知的 special rule 類型: {rtype}")

    if lines_to_delete:
        xml_lines = [
            line for i, line in enumerate(xml_lines, start=1)
            if i not in lines_to_delete
        ]
    return xml_lines, msgs


def validate_template(template):
    """驗證匯出檔名格式;失敗時 raise ValueError"""
    if not isinstance(template, str) or not template.strip():
        raise ValueError("匯出檔案檔名格式不可為空")
    placeholders = PLACEHOLDER_RE.findall(template)
    if not placeholders:
        raise ValueError("匯出檔案檔名格式至少需要一個欄位佔位符,例如 {C}")
    for p in placeholders:
        try:
            col_to_index(p)
        except ValueError as e:
            raise ValueError(f"檔名格式中的 {{{p}}} 無效: {e}")


def migrate_config(cfg):
    """把舊版 config (key_column / key_column_label) 轉成新版 filename_template"""
    if not isinstance(cfg, dict):
        return cfg
    if not cfg.get("filename_template"):
        kc = cfg.get("key_column")
        if kc is not None:
            try:
                cfg["filename_template"] = f"{{{index_to_letter(col_to_index(kc))}}}"
            except Exception:
                cfg["filename_template"] = ""
        else:
            # 無舊版 key_column 時保持空白,讓使用者自行設定;
            # 轉換前 validate_template 仍會擋下空白檔名格式。
            cfg["filename_template"] = cfg.get("filename_template", "") or ""
    cfg.pop("key_column", None)
    cfg.pop("key_column_label", None)
    cfg.pop("_description", None)
    if not isinstance(cfg.get("min_required_columns"), int) or cfg["min_required_columns"] < 1:
        cfg["min_required_columns"] = 1
    return cfg


def validate_config(cfg):
    """檢查 config 結構,失敗時 raise ValueError"""
    if not isinstance(cfg, dict):
        raise ValueError("設定必須是 JSON 物件")
    for k in ("filename_template", "mappings"):
        if k not in cfg:
            raise ValueError(f"缺少必要欄位: {k}")
    # 允許空白檔名格式 (空白起始設定);實際轉換時 _convert 會再以
    # validate_template 擋下,並提示使用者先填寫。
    tmpl = cfg["filename_template"]
    if isinstance(tmpl, str) and tmpl.strip():
        validate_template(tmpl)
    min_cols = cfg.get("min_required_columns", 1)
    if not isinstance(min_cols, int) or min_cols < 1:
        raise ValueError("min_required_columns 必須為正整數")
    if not isinstance(cfg["mappings"], list):
        raise ValueError("mappings 必須是陣列")
    for i, m in enumerate(cfg["mappings"]):
        if not isinstance(m, dict):
            raise ValueError(f"mappings[{i}] 必須是物件")
        for k in ("column", "line", "tag"):
            if k not in m:
                raise ValueError(f"mappings[{i}] 缺少 {k}")
        col_to_index(m["column"])
        if not isinstance(m["line"], int) or m["line"] < 1:
            raise ValueError(f"mappings[{i}].line 必須為 ≥1 的整數")
        if not isinstance(m["tag"], str) or not m["tag"].strip():
            raise ValueError(f"mappings[{i}].tag 不可為空")
    rules = cfg.get("special_rules", [])
    if not isinstance(rules, list):
        raise ValueError("special_rules 必須是陣列")


# ---------- 主應用 ----------

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("XML 批次轉換工具")
        self.root.geometry("1010x720")
        self.root.minsize(900, 600)

        self.config_path = app_dir() / CONFIG_FILE
        self.config = None
        self.config_error = None

        self._setup_style()
        self._build_ui()
        self._load_config(initial=True)

    # ---- 樣式 ----
    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        font_main = ("Microsoft JhengHei UI", 10)
        self.root.option_add("*Font", font_main)

        style.configure("TNotebook", background="#f0f0f0", borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            padding=[18, 8],
            font=("Microsoft JhengHei UI", 10),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#ffffff"), ("!selected", "#d9d9d9")],
            foreground=[("selected", "#000000"), ("!selected", "#333333")],
        )
        style.configure("TLabelframe", padding=8)
        style.configure("TLabelframe.Label", font=("Microsoft JhengHei UI", 10, "bold"))
        style.configure("TButton", padding=[10, 4])
        style.configure("Accent.TButton", padding=[20, 6], font=("Microsoft JhengHei UI", 10, "bold"))

    # ---- UI ----
    def _build_ui(self):
        # 頁簽
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self.tab_convert = ttk.Frame(self.notebook)
        self.tab_config = ttk.Frame(self.notebook)
        self.tab_samples = ttk.Frame(self.notebook)
        self.tab_help = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_convert, text="CSV產生XML")
        self.notebook.add(self.tab_config, text="設定檔")
        self.notebook.add(self.tab_samples, text="範例下載")
        self.notebook.add(self.tab_help, text="說明")

        self._build_convert_tab()
        self._build_config_tab()
        self._build_samples_tab()
        self._build_help_tab()

        # 底部狀態列
        self.status_var = tk.StringVar(value="就緒")
        status_bar = tk.Label(
            self.root, textvariable=self.status_var, anchor="w",
            bd=1, relief="sunken", bg="#ececec", padx=8,
        )
        status_bar.pack(side="bottom", fill="x")

    def _build_convert_tab(self):
        frm = self.tab_convert

        # 設定區
        settings = ttk.LabelFrame(frm, text="設定")
        settings.pack(fill="x", padx=12, pady=(10, 6))

        ttk.Label(settings, text="CSV 對應表:").grid(row=0, column=0, sticky="e", padx=(10, 6), pady=8)
        self.csv_entry = ttk.Entry(settings)
        self.csv_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=8)
        ttk.Button(settings, text="瀏覽", width=8, command=self._browse_csv).grid(row=0, column=2, padx=10, pady=8)

        ttk.Label(settings, text="範例 XML:").grid(row=1, column=0, sticky="e", padx=(10, 6), pady=8)
        self.xml_entry = ttk.Entry(settings)
        self.xml_entry.grid(row=1, column=1, sticky="ew", padx=4, pady=8)
        ttk.Button(settings, text="瀏覽", width=8, command=self._browse_xml).grid(row=1, column=2, padx=10, pady=8)

        ttk.Label(settings, text="輸出資料夾:").grid(row=2, column=0, sticky="e", padx=(10, 6), pady=8)
        self.out_entry = ttk.Entry(settings)
        self.out_entry.grid(row=2, column=1, sticky="ew", padx=4, pady=8)
        ttk.Button(settings, text="瀏覽", width=8, command=self._browse_out).grid(row=2, column=2, padx=10, pady=8)

        settings.columnconfigure(1, weight=1)

        # 動作列
        action_bar = tk.Frame(frm)
        action_bar.pack(fill="x", padx=12, pady=4)
        self.btn_convert = ttk.Button(action_bar, text="開始轉換", style="Accent.TButton", command=self._convert)
        self.btn_convert.pack(pady=4)

        # 進度條
        self.progress = ttk.Progressbar(frm, mode="determinate")
        self.progress.pack(fill="x", padx=12, pady=4)

        # 結果摘要列
        self.result_var = tk.StringVar(value="")
        self.result_label = tk.Label(
            frm, textvariable=self.result_var, anchor="w",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg="#f0f0f0", fg="#333", padx=12, pady=4,
        )
        self.result_label.pack(fill="x", padx=12)

        # 預覽 / 結果區
        preview_frm = ttk.LabelFrame(frm, text="CSV 預覽 / 執行結果")
        preview_frm.pack(fill="both", expand=True, padx=12, pady=(6, 12))

        preview_inner = tk.Frame(preview_frm)
        preview_inner.pack(fill="both", expand=True)

        self.tree_preview = ttk.Treeview(preview_inner, show="headings", height=14)
        ys = ttk.Scrollbar(preview_inner, orient="vertical", command=self.tree_preview.yview)
        xs = ttk.Scrollbar(preview_inner, orient="horizontal", command=self.tree_preview.xview)
        self.tree_preview.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.tree_preview.pack(fill="both", expand=True)

        # 列狀態顏色 tag
        self.tree_preview.tag_configure("ok", background="#e8f5e9", foreground="#1a7f1a")
        self.tree_preview.tag_configure("err", background="#fdecea", foreground="#c0392b")
        self.tree_preview.tag_configure("warn", background="#fff8e1", foreground="#8a6d00")

        # 預覽資料 (供轉換時使用)
        self._csv_headers = []
        self._csv_rows = []
        self._csv_loaded_path = ""

    def _build_config_tab(self):
        frm = self.tab_config

        # 路徑與狀態
        info = ttk.LabelFrame(frm, text="設定檔")
        info.pack(fill="x", padx=12, pady=(10, 6))
        ttk.Label(info, text="路徑:").grid(row=0, column=0, sticky="e", padx=8, pady=4)
        self.config_path_var = tk.StringVar(value=str(self.config_path))
        ttk.Entry(info, textvariable=self.config_path_var, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=4, pady=4
        )
        ttk.Button(info, text="瀏覽", width=8, command=self._browse_config).grid(
            row=0, column=2, padx=(4, 8), pady=4
        )
        ttk.Button(info, text="另存新檔", width=10, command=self._save_config_as).grid(
            row=0, column=3, padx=(0, 8), pady=4
        )
        ttk.Label(info, text="狀態:").grid(row=1, column=0, sticky="e", padx=8, pady=4)
        self.config_status_var = tk.StringVar(value="尚未載入")
        self.config_status_label = ttk.Label(info, textvariable=self.config_status_var)
        self.config_status_label.grid(row=1, column=1, columnspan=3, sticky="w", padx=4, pady=4)
        info.columnconfigure(1, weight=1)

        # 按鈕列
        btns = tk.Frame(frm)
        btns.pack(fill="x", padx=12, pady=(4, 4))
        ttk.Button(btns, text="重新載入", command=self._reload_config).pack(side="left", padx=4)
        ttk.Button(btns, text="下載預設設定", command=self._download_default_config).pack(side="left", padx=4)

        # 編輯器容器 (內嵌 ConfigEditor)
        self.editor_container = tk.Frame(frm)
        self.editor_container.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.editor = None

    def _build_samples_tab(self):
        frm = self.tab_samples

        intro = tk.Label(
            frm,
            text="下載以下範例檔,照著格式準備自己的資料。",
            anchor="w", justify="left", bg="#f0f0f0", fg="#333",
            font=("Microsoft JhengHei UI", 10), padx=4, pady=8,
        )
        intro.pack(fill="x", padx=16, pady=(14, 4))

        box = ttk.LabelFrame(frm, text="範例檔")
        box.pack(fill="x", padx=16, pady=6)
        box.columnconfigure(0, weight=1)

        items = [
            ("CSV 對應表範例", "每列一筆資料,第一列為欄位名稱。",
             "下載範例 CSV", self._download_sample_csv),
            ("範例 XML 模板", "套用對應規則的基礎 XML。",
             "下載範例 XML", self._download_sample_xml),
            ("範例設定 (config.json)", "完整欄位對應與特殊規則,可直接匯入參考。",
             "下載範例設定", self._download_sample_config),
        ]
        for i, (title, desc, btn_text, cmd) in enumerate(items):
            row = ttk.Frame(box)
            row.grid(row=2 * i, column=0, sticky="ew", padx=10, pady=8)
            row.columnconfigure(0, weight=1)
            tk.Label(
                row, text=title, anchor="w",
                font=("Microsoft JhengHei UI", 10, "bold"),
            ).grid(row=0, column=0, sticky="w")
            tk.Label(
                row, text=desc, anchor="w", fg="#666",
                font=("Microsoft JhengHei UI", 9),
            ).grid(row=1, column=0, sticky="w")
            ttk.Button(row, text=btn_text, width=16, command=cmd).grid(
                row=0, column=1, rowspan=2, padx=(12, 4))
            if i < len(items) - 1:
                ttk.Separator(box, orient="horizontal").grid(
                    row=2 * i + 1, column=0, sticky="ew", padx=10)

    def _rebuild_editor(self):
        """銷毀並重建編輯器,讓 UI 跟最新的 self.config 同步"""
        if self.editor is not None:
            self.editor.destroy()
            self.editor = None
        if self.config is not None:
            base = json.loads(json.dumps(self.config))
            self.editor = ConfigEditor(self.editor_container, base, on_save=self._save_from_editor)
            self.editor.pack(fill="both", expand=True)

    # ---- 設定檔操作 ----
    def _load_config(self, initial=False):
        try:
            if not self.config_path.exists():
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg = migrate_config(cfg)
            validate_config(cfg)
            self.config = cfg
            self.config_error = None
            self.config_status_var.set("✓ 已載入")
            self.config_status_label.configure(foreground="#1a7f1a")
            self.status_var.set(f"設定已載入: {len(cfg['mappings'])} 條 mapping、{len(cfg.get('special_rules', []))} 條 rule")
        except Exception as e:
            self.config = None
            self.config_error = str(e)
            self.config_status_var.set(f"✗ 載入失敗: {e}")
            self.config_status_label.configure(foreground="#c0392b")
            self.status_var.set(f"設定檔錯誤: {e}")
            # 載入失敗時用預設值給編輯器,避免空白
            self.config = json.loads(json.dumps(DEFAULT_CONFIG))
        self._rebuild_editor()

    def _reload_config(self):
        self._load_config()

    def _browse_config(self):
        """挑選其他 config 檔。檔案不存在會擋下不允許。"""
        p = filedialog.askopenfilename(
            title="選擇設定檔",
            filetypes=[("JSON", "*.json"), ("所有檔案", "*.*")],
            initialdir=str(self.config_path.parent),
            initialfile=self.config_path.name,
        )
        if not p:
            return
        self.config_path = Path(p)
        self.config_path_var.set(str(self.config_path))
        self._load_config()

    def _save_config_as(self):
        """把目前設定另存到使用者指定的檔案,並切換成新的目前路徑。"""
        if self.config is None:
            messagebox.showerror("錯誤", "目前沒有可儲存的設定。")
            return
        p = filedialog.asksaveasfilename(
            title="另存設定檔",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("所有檔案", "*.*")],
            initialdir=str(self.config_path.parent),
            initialfile=self.config_path.name,
        )
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("錯誤", f"寫入失敗: {e}")
            return
        self.config_path = Path(p)
        self.config_path_var.set(str(self.config_path))
        self._load_config()
        messagebox.showinfo("完成", f"已另存至:\n{p}")

    def _download_default_config(self):
        """把內建的預設設定另存到使用者指定的 .json 檔。
        不影響目前載入的設定,也不會切換目前路徑。"""
        p = filedialog.asksaveasfilename(
            title="下載預設設定",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("所有檔案", "*.*")],
            initialdir=str(self.config_path.parent),
            initialfile="config_default.json",
        )
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("錯誤", f"寫入失敗: {e}")
            return
        messagebox.showinfo("完成", f"預設設定已下載至:\n{p}")

    def _download_sample_config(self):
        """把完整範例設定 (含 mappings/rules) 另存,供使用者參考或匯入。"""
        p = filedialog.asksaveasfilename(
            title="下載範例設定",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("所有檔案", "*.*")],
            initialdir=str(self.config_path.parent),
            initialfile="config.json",
        )
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(SAMPLE_CONFIG, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("錯誤", f"寫入失敗: {e}")
            return
        messagebox.showinfo("完成", f"範例設定已下載至:\n{p}")

    def _download_text(self, res_name, content, title, ftypes):
        """把內嵌的範例內容寫到使用者指定位置 (UTF-8, 保留原始換行)。"""
        suffix = Path(res_name).suffix
        p = filedialog.asksaveasfilename(
            title=title,
            defaultextension=suffix,
            filetypes=ftypes,
            initialdir=str(self.config_path.parent),
            initialfile=res_name,
        )
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        except Exception as e:
            messagebox.showerror("錯誤", f"寫入失敗: {e}")
            return
        messagebox.showinfo("完成", f"範例已下載至:\n{p}")

    def _download_sample_csv(self):
        self._download_text(
            SAMPLE_CSV_NAME, SAMPLE_CSV_TEXT, "下載範例 CSV",
            [("CSV", "*.csv"), ("所有檔案", "*.*")],
        )

    def _download_sample_xml(self):
        self._download_text(
            SAMPLE_XML_NAME, SAMPLE_XML_TEXT, "下載範例 XML",
            [("XML", "*.xml"), ("所有檔案", "*.*")],
        )

    def _save_from_editor(self, new_cfg):
        """ConfigEditor 呼叫的儲存入口 (含驗證 + 寫檔)。失敗只在狀態列顯示,不彈窗。"""
        try:
            validate_config(new_cfg)
        except Exception as e:
            self.config_status_var.set(f"⚠ 驗證失敗 (未儲存): {e}")
            self.config_status_label.configure(foreground="#c0392b")
            self.status_var.set(f"⚠ 設定驗證失敗: {e}")
            return False
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(new_cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.config_status_var.set(f"✗ 寫入失敗: {e}")
            self.config_status_label.configure(foreground="#c0392b")
            return False
        # 寫入成功後就地更新狀態
        self.config = new_cfg
        self.config_error = None
        self.config_status_var.set("✓ 已自動儲存")
        self.config_status_label.configure(foreground="#1a7f1a")
        self.status_var.set(
            f"設定已儲存: {len(new_cfg['mappings'])} 條 mapping、{len(new_cfg.get('special_rules', []))} 條 rule"
        )
        return True

    # ---- 瀏覽 ----
    def _browse_csv(self):
        p = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("所有檔案", "*.*")])
        if p:
            self.csv_entry.delete(0, "end")
            self.csv_entry.insert(0, p)
            self._load_csv_preview(p)

    def _browse_xml(self):
        p = filedialog.askopenfilename(filetypes=[("XML", "*.xml"), ("所有檔案", "*.*")])
        if p:
            self.xml_entry.delete(0, "end")
            self.xml_entry.insert(0, p)

    def _browse_out(self):
        p = filedialog.askdirectory()
        if p:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, p)

    # ---- CSV 預覽 ----
    def _read_csv(self, path):
        """讀 CSV,自動嘗試 utf-8-sig → cp950。回傳 (rows, encoding) 或 raise。"""
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                return list(csv.reader(f)), "utf-8"
        except UnicodeDecodeError:
            with open(path, "r", encoding="cp950", newline="") as f:
                return list(csv.reader(f)), "cp950"

    def _load_csv_preview(self, path):
        """載入 CSV、把表頭與資料填到下方 Treeview。失敗會在狀態列顯示錯誤。"""
        try:
            rows, enc = self._read_csv(path)
        except Exception as e:
            messagebox.showerror("錯誤", f"CSV 讀取失敗: {e}")
            self._csv_headers = []
            self._csv_rows = []
            self._csv_loaded_path = ""
            return False

        if not rows:
            messagebox.showerror("錯誤", "CSV 為空。")
            return False

        headers = rows[0]
        data_rows = rows[1:]
        self._csv_headers = headers
        self._csv_rows = data_rows
        self._csv_loaded_path = path

        # 重建 Treeview 欄位 (列號 + CSV 各欄 + 狀態)
        col_ids = ["__row__"] + [f"c{i}" for i in range(len(headers))] + ["__status__"]
        self.tree_preview.configure(columns=col_ids)

        self.tree_preview.heading("__row__", text="#")
        self.tree_preview.column("__row__", width=50, anchor="center", stretch=False)
        for i, h in enumerate(headers):
            cid = f"c{i}"
            label = h.strip() if h and h.strip() else f"欄{index_to_letter(i)}"
            self.tree_preview.heading(cid, text=label)
            self.tree_preview.column(cid, width=110, anchor="w", stretch=False)
        self.tree_preview.heading("__status__", text="狀態")
        self.tree_preview.column("__status__", width=260, anchor="w", stretch=True)

        # 清空舊資料並重新填
        self.tree_preview.delete(*self.tree_preview.get_children())
        for idx, row in enumerate(data_rows, start=1):
            cells = [str(idx)] + [
                row[i] if i < len(row) else "" for i in range(len(headers))
            ] + [""]
            self.tree_preview.insert("", "end", iid=f"r{idx}", values=cells)

        self.result_var.set("")
        self.status_var.set(f"CSV 已載入:{len(data_rows)} 筆,{len(headers)} 欄 ({enc})")
        return True

    def _set_row_status(self, idx, msg, tag=""):
        iid = f"r{idx}"
        if not self.tree_preview.exists(iid):
            return
        vals = list(self.tree_preview.item(iid, "values"))
        if vals:
            vals[-1] = msg
            self.tree_preview.item(iid, values=vals, tags=(tag,) if tag else ())
            self.tree_preview.see(iid)

    # ---- 轉換主流程 ----
    def _convert(self):
        if self.config is None:
            messagebox.showerror("錯誤", f"設定檔尚未載入:\n{self.config_error or ''}")
            return

        csv_path = self.csv_entry.get().strip()
        xml_path = self.xml_entry.get().strip()
        out_dir = self.out_entry.get().strip()

        if not csv_path or not xml_path or not out_dir:
            messagebox.showerror("錯誤", "請選擇 CSV、範例 XML 與輸出資料夾。")
            return
        if not Path(csv_path).is_file():
            messagebox.showerror("錯誤", f"CSV 不存在:\n{csv_path}")
            return
        if not Path(xml_path).is_file():
            messagebox.showerror("錯誤", f"範例 XML 不存在:\n{xml_path}")
            return

        cfg = self.config
        template = cfg.get("filename_template", "{C}")
        try:
            validate_template(template)
        except ValueError as e:
            messagebox.showerror("錯誤", f"匯出檔案檔名格式錯誤: {e}")
            return

        try:
            with open(xml_path, "r", encoding="utf-8") as f:
                xml_template = f.readlines()
        except Exception as e:
            messagebox.showerror("錯誤", f"範例 XML 讀取失敗: {e}")
            return

        max_line = max((int(m["line"]) for m in cfg["mappings"]), default=0)
        xml_warning = None
        if len(xml_template) < max_line:
            xml_warning = f"範例 XML 行數 {len(xml_template)} 小於 mappings 最大行 {max_line}"

        # 確保 CSV 已預覽,且路徑與目前載入的相符
        if csv_path != self._csv_loaded_path or not self._csv_rows:
            if not self._load_csv_preview(csv_path):
                return

        data_rows = self._csv_rows
        Path(out_dir).mkdir(parents=True, exist_ok=True)

        # 先計算每列的輸出檔名(不含副檔名),並偵測重複
        resolved_names = [build_filename(template, row) for row in data_rows]
        seen = {}
        dup_names = set()
        for i, name in enumerate(resolved_names):
            if not name or name == "_":
                continue
            if name in seen:
                dup_names.add(name)
            else:
                seen[name] = i

        # 清掉前次的列狀態
        for idx in range(1, len(data_rows) + 1):
            self._set_row_status(idx, "", "")

        # 重設 UI
        self.btn_convert.configure(state="disabled")
        self.progress["maximum"] = max(len(data_rows), 1)
        self.progress["value"] = 0
        if xml_warning:
            self.result_var.set(f"⚠ {xml_warning}")
            self.result_label.configure(fg="#8a6d00")
        else:
            self.result_var.set(f"轉換中… (共 {len(data_rows)} 筆)")
            self.result_label.configure(fg="#333")

        success = fail = skipped = 0
        min_cols = cfg.get("min_required_columns", 1)

        try:
            for idx, row in enumerate(data_rows, start=1):
                self.progress["value"] = idx
                if self.root.winfo_exists():
                    self.root.update_idletasks()

                if len(row) < min_cols:
                    self._set_row_status(idx, f"略過: 欄位數 {len(row)} < {min_cols}", "warn")
                    skipped += 1
                    continue

                name = resolved_names[idx - 1]
                if not name or name == "_":
                    self._set_row_status(idx, "略過: 套用檔名格式後為空", "warn")
                    skipped += 1
                    continue

                if name in dup_names:
                    self._set_row_status(idx, f"✗ 檔名重複 [{name}],未輸出", "err")
                    fail += 1
                    continue

                try:
                    lines = list(xml_template)
                    lines, m1 = apply_mappings(lines, row, cfg["mappings"])
                    lines, m2 = apply_special_rules(lines, row, cfg.get("special_rules", []))
                    out_name = name + ".xml"
                    out_path = Path(out_dir) / out_name
                    with open(out_path, "w", encoding="utf-8", newline="") as f:
                        f.writelines(lines)
                    warn_str = "; ".join(m1 + m2)
                    msg = f"✓ {out_name}" + (f"  ⚠ {warn_str}" if warn_str else "")
                    self._set_row_status(idx, msg, "ok")
                    success += 1
                except Exception as e:
                    self._set_row_status(idx, f"✗ 失敗: {e}", "err")
                    fail += 1
        finally:
            self.btn_convert.configure(state="normal")

        summary = f"完成 — 成功 {success}、失敗 {fail}、略過 {skipped}(共 {len(data_rows)} 筆)"
        self.result_var.set(summary)
        self.result_label.configure(
            fg="#1a7f1a" if fail == 0 and skipped == 0 else ("#c0392b" if fail else "#8a6d00")
        )
        self.status_var.set(summary)
        messagebox.showinfo("執行結果", summary)

    def _build_help_tab(self):
        body = tk.Frame(self.tab_help)
        body.pack(fill="both", expand=True, padx=14, pady=10)
        txt = tk.Text(body, wrap="word", font=("Microsoft JhengHei UI", 10),
                      padx=10, pady=10, relief="flat", bg="#fafafa")
        ys = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        txt.tag_configure("h1", font=("Microsoft JhengHei UI", 13, "bold"),
                          foreground="#1a3d5c", spacing1=12, spacing3=6)
        txt.tag_configure("h2", font=("Microsoft JhengHei UI", 11, "bold"),
                          foreground="#333333", spacing1=8, spacing3=4)
        txt.tag_configure("p", spacing1=2, spacing3=4, lmargin1=8, lmargin2=8)
        txt.tag_configure("li", spacing1=1, spacing3=2, lmargin1=20, lmargin2=36)
        txt.tag_configure("code", font=("Consolas", 10), background="#eef2f6",
                          spacing1=4, spacing3=8, lmargin1=20, lmargin2=20,
                          rmargin=20)
        txt.tag_configure("note", font=("Microsoft JhengHei UI", 10, "italic"),
                          foreground="#8a4500", background="#fff5e6",
                          spacing1=4, spacing3=8, lmargin1=12, lmargin2=12,
                          rmargin=12)

        for tag, content in HELP_TEXT:
            if tag == "li":
                txt.insert("end", "・" + content + "\n", "li")
            else:
                txt.insert("end", content + "\n", tag)
        txt.configure(state="disabled")


# ---------- 結構化編輯器 ----------

class ConfigEditor(ttk.Frame):
    def __init__(self, parent, cfg, on_save):
        super().__init__(parent)
        self.cfg = cfg
        self.on_save = on_save  # 失敗會把訊息寫到 App 的狀態列,不彈窗
        self._autosave_after_id = None

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=2, pady=2)

        self.tab_general = ttk.Frame(nb)
        self.tab_mappings = ttk.Frame(nb)
        self.tab_rules = ttk.Frame(nb)
        nb.add(self.tab_general, text="匯出檔名規則")
        nb.add(self.tab_mappings, text="欄位對應")
        nb.add(self.tab_rules, text="特殊規則")

        self._build_general()
        self._build_mappings()
        self._build_rules()

        # 文字欄位變動 → 防抖 500ms 後自動儲存 (避免邊打邊存)
        self.var_template.trace_add("write", lambda *_: self._schedule_autosave())

        # 銷毀時取消未觸發的 timer
        self.bind("<Destroy>", self._on_destroy)

    # ---- 自動儲存 ----
    def _schedule_autosave(self, delay_ms=500):
        if self._autosave_after_id is not None:
            try:
                self.after_cancel(self._autosave_after_id)
            except Exception:
                pass
        self._autosave_after_id = self.after(delay_ms, self._do_autosave)

    def _do_autosave(self):
        self._autosave_after_id = None
        new_cfg = self._collect_cfg()
        self.on_save(new_cfg)

    def _on_destroy(self, _event=None):
        if self._autosave_after_id is not None:
            try:
                self.after_cancel(self._autosave_after_id)
            except Exception:
                pass
            self._autosave_after_id = None

    def _build_general(self):
        f = self.tab_general
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="匯出檔案檔名:").grid(
            row=0, column=0, sticky="e", padx=10, pady=(16, 6))
        self.var_template = tk.StringVar(
            value=str(self.cfg.get("filename_template", "{C}"))
        )
        ttk.Entry(f, textvariable=self.var_template, width=40).grid(
            row=0, column=1, sticky="ew", padx=4, pady=(16, 6))

        # 規則說明 (顯示在設定區最下方)
        help_box = tk.Text(
            f, wrap="word", height=10, relief="flat", bg="#fafafa",
            font=("Microsoft JhengHei UI", 10), padx=12, pady=10,
        )
        help_box.grid(row=1, column=0, columnspan=2,
                      sticky="nsew", padx=10, pady=(12, 12))
        f.rowconfigure(1, weight=1)

        help_box.tag_configure(
            "h", font=("Microsoft JhengHei UI", 11, "bold"),
            foreground="#1a3d5c", spacing1=2, spacing3=6)
        help_box.tag_configure("p", spacing3=4)
        help_box.tag_configure(
            "code", font=("Consolas", 10), background="#eef2f6",
            lmargin1=16, lmargin2=16, rmargin=16, spacing1=4, spacing3=8)

        help_box.insert("end", "匯出檔名規則\n", "h")
        help_box.insert(
            "end",
            "用 {A} {B} {C}… 代表 CSV 對應欄位的值,其餘文字原樣保留。\n"
            "副檔名 .xml 會自動加上。同樣檔名的兩筆資料會被擋下不輸出。\n",
            "p")
        help_box.insert("end", "範例\n", "h")
        help_box.insert(
            "end",
            "{C}            →  C欄值.xml\n"
            "XML_{C}        →  XML_C欄值.xml\n"
            "{C}_{A}        →  C欄值_A欄值.xml\n"
            "114年_{C}_v1   →  114年_C欄值_v1.xml",
            "code")
        help_box.configure(state="disabled")

        # 保留 min_required_columns,儲存時帶回去
        self._kept_min_cols = self.cfg.get("min_required_columns", 1)

    def _build_mappings(self):
        f = self.tab_mappings
        list_frame = ttk.Frame(f)
        list_frame.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=8)

        cols = ("column", "line", "tag", "note")
        self.tree_m = ttk.Treeview(list_frame, columns=cols, show="headings", height=14)
        self.tree_m.heading("column", text="欄位")
        self.tree_m.heading("line", text="行號")
        self.tree_m.heading("tag", text="標籤")
        self.tree_m.heading("note", text="備註")
        self.tree_m.column("column", width=80, anchor="center")
        self.tree_m.column("line", width=80, anchor="center")
        self.tree_m.column("tag", width=180)
        self.tree_m.column("note", width=200)
        ys = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree_m.yview)
        self.tree_m.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.tree_m.pack(fill="both", expand=True)

        for m in self.cfg.get("mappings", []):
            self.tree_m.insert("", "end", values=(col_display(m.get("column")), m.get("line"), m.get("tag"), m.get("note", "")))

        btn_frame = ttk.Frame(f)
        btn_frame.pack(side="right", fill="y", padx=(4, 8), pady=8)
        ttk.Button(btn_frame, text="新增", command=lambda: self._edit_mapping(None)).pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="編輯", command=self._edit_mapping_selected).pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="刪除", command=lambda: self._delete_selected(self.tree_m)).pack(fill="x", pady=2)
        ttk.Separator(btn_frame, orient="horizontal").pack(fill="x", pady=4)
        ttk.Button(btn_frame, text="↑ 上移", command=lambda: self._move(self.tree_m, -1)).pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="↓ 下移", command=lambda: self._move(self.tree_m, 1)).pack(fill="x", pady=2)

        self.tree_m.bind("<Double-1>", lambda e: self._edit_mapping_selected())

    def _build_rules(self):
        f = self.tab_rules
        list_frame = ttk.Frame(f)
        list_frame.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=8)

        cols = ("name", "type", "column", "lines")
        self.tree_r = ttk.Treeview(list_frame, columns=cols, show="headings", height=14)
        self.tree_r.heading("name", text="名稱")
        self.tree_r.heading("type", text="類型")
        self.tree_r.heading("column", text="欄位")
        self.tree_r.heading("lines", text="行號")
        self.tree_r.column("name", width=200)
        self.tree_r.column("type", width=220)
        self.tree_r.column("column", width=80, anchor="center")
        self.tree_r.column("lines", width=160)
        ys = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree_r.yview)
        self.tree_r.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.tree_r.pack(fill="both", expand=True)

        for r in self.cfg.get("special_rules", []):
            self.tree_r.insert("", "end", values=(
                r.get("name", ""),
                rule_type_label(r.get("type", "")),
                col_display(r.get("column", "")),
                ",".join(str(x) for x in r.get("lines", [])),
            ))

        btn_frame = ttk.Frame(f)
        btn_frame.pack(side="right", fill="y", padx=(4, 8), pady=8)
        ttk.Button(btn_frame, text="新增", command=lambda: self._edit_rule(None)).pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="編輯", command=self._edit_rule_selected).pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="刪除", command=lambda: self._delete_selected(self.tree_r)).pack(fill="x", pady=2)
        ttk.Separator(btn_frame, orient="horizontal").pack(fill="x", pady=4)
        ttk.Button(btn_frame, text="↑ 上移", command=lambda: self._move(self.tree_r, -1)).pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="↓ 下移", command=lambda: self._move(self.tree_r, 1)).pack(fill="x", pady=2)

        self.tree_r.bind("<Double-1>", lambda e: self._edit_rule_selected())

    # ---- treeview 共用 ----
    def _delete_selected(self, tree):
        sel = tree.selection()
        if not sel:
            return
        for iid in sel:
            tree.delete(iid)
        self._do_autosave()

    def _move(self, tree, delta):
        sel = tree.selection()
        if not sel:
            return
        iid = sel[0]
        idx = tree.index(iid)
        new_idx = idx + delta
        if 0 <= new_idx < len(tree.get_children()):
            tree.move(iid, "", new_idx)
            self._do_autosave()

    # ---- mapping 編輯 ----
    def _edit_mapping_selected(self):
        sel = self.tree_m.selection()
        if not sel:
            return
        self._edit_mapping(sel[0])

    def _edit_mapping(self, iid):
        if iid is None:
            initial = {"column": "A", "line": 1, "tag": "gco:CharacterString", "note": ""}
        else:
            v = self.tree_m.item(iid)["values"]
            initial = {"column": str(v[0]), "line": str(v[1]), "tag": str(v[2]), "note": str(v[3])}
        result = MappingDialog(self, initial).result
        if result is None:
            return
        values = (col_display(result["column"]), result["line"], result["tag"], result["note"])
        if iid is None:
            self.tree_m.insert("", "end", values=values)
        else:
            self.tree_m.item(iid, values=values)
        self._do_autosave()

    # ---- rule 編輯 ----
    def _edit_rule_selected(self):
        sel = self.tree_r.selection()
        if not sel:
            return
        self._edit_rule(sel[0])

    def _edit_rule(self, iid):
        if iid is None:
            initial = {"name": "", "type": "clear_lines_if_column_empty", "column": "A", "lines": ""}
        else:
            v = self.tree_r.item(iid)["values"]
            initial = {
                "name": str(v[0]),
                "type": rule_type_key(str(v[1])),
                "column": str(v[2]),
                "lines": str(v[3]),
            }
        result = RuleDialog(self, initial).result
        if result is None:
            return
        values = (
            result["name"],
            rule_type_label(result["type"]),
            col_display(result["column"]),
            ",".join(str(x) for x in result["lines"]),
        )
        if iid is None:
            self.tree_r.insert("", "end", values=values)
        else:
            self.tree_r.item(iid, values=values)
        self._do_autosave()

    # ---- 儲存 ----
    def _collect_cfg(self):
        """從 GUI 收集成 config dict。會盡量寬鬆,讓 validate 在 on_save 一條龍報錯。"""
        new_cfg = {
            "filename_template": self.var_template.get().strip(),
            "min_required_columns": self._kept_min_cols if isinstance(self._kept_min_cols, int) else 1,
            "mappings": [],
            "special_rules": [],
        }

        # mappings
        for iid in self.tree_m.get_children():
            v = self.tree_m.item(iid)["values"]
            try:
                line_no = int(v[1])
            except (ValueError, TypeError):
                line_no = 0  # 讓 validate_config 抓
            new_cfg["mappings"].append({
                "column": str(v[0]),
                "line": line_no,
                "tag": str(v[2]),
                "note": str(v[3]) if len(v) > 3 else "",
            })

        # rules
        for iid in self.tree_r.get_children():
            v = self.tree_r.item(iid)["values"]
            lines_str = str(v[3]).strip() if len(v) > 3 else ""
            lines = []
            for x in lines_str.split(","):
                x = x.strip()
                if not x:
                    continue
                try:
                    lines.append(int(x))
                except ValueError:
                    pass
            new_cfg["special_rules"].append({
                "name": str(v[0]),
                "type": rule_type_key(str(v[1])),
                "column": str(v[2]),
                "lines": lines,
            })

        return new_cfg


class MappingDialog(tk.Toplevel):
    def __init__(self, parent, initial):
        super().__init__(parent)
        self.title("編輯欄位對應")
        self.transient(parent)
        self.grab_set()
        self.result = None

        ttk.Label(self, text="欄位 (字母 A/B/C 或 0/1/2):").grid(row=0, column=0, sticky="e", padx=10, pady=6)
        self.var_col = tk.StringVar(value=str(initial.get("column", "")))
        ttk.Entry(self, textvariable=self.var_col, width=12).grid(row=0, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(self, text="行號 (≥1):").grid(row=1, column=0, sticky="e", padx=10, pady=6)
        self.var_line = tk.StringVar(value=str(initial.get("line", "")))
        ttk.Entry(self, textvariable=self.var_line, width=12).grid(row=1, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(self, text="標籤:").grid(row=2, column=0, sticky="e", padx=10, pady=6)
        self.var_tag = tk.StringVar(value=str(initial.get("tag", "")))
        ttk.Entry(self, textvariable=self.var_tag, width=30).grid(row=2, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(self, text="備註:").grid(row=3, column=0, sticky="e", padx=10, pady=6)
        self.var_note = tk.StringVar(value=str(initial.get("note", "")))
        ttk.Entry(self, textvariable=self.var_note, width=30).grid(row=3, column=1, sticky="w", padx=6, pady=6)

        bar = ttk.Frame(self)
        bar.grid(row=4, column=0, columnspan=2, pady=10)
        ttk.Button(bar, text="確定", command=self._ok).pack(side="left", padx=4)
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="left", padx=4)

        self.wait_window()

    def _ok(self):
        try:
            col_to_index(self.var_col.get().strip())
        except Exception as e:
            messagebox.showerror("驗證失敗", f"欄位格式錯誤: {e}", parent=self)
            return
        try:
            line = int(self.var_line.get().strip())
            if line < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("驗證失敗", "行號必須為 ≥1 的整數", parent=self)
            return
        tag = self.var_tag.get().strip()
        if not tag:
            messagebox.showerror("驗證失敗", "標籤不可為空", parent=self)
            return
        self.result = {
            "column": self.var_col.get().strip(),
            "line": line,
            "tag": tag,
            "note": self.var_note.get().strip(),
        }
        self.destroy()


class RuleDialog(tk.Toplevel):
    def __init__(self, parent, initial):
        super().__init__(parent)
        self.title("編輯特殊規則")
        self.transient(parent)
        self.grab_set()
        self.result = None

        ttk.Label(self, text="名稱:").grid(row=0, column=0, sticky="e", padx=10, pady=6)
        self.var_name = tk.StringVar(value=str(initial.get("name", "")))
        ttk.Entry(self, textvariable=self.var_name, width=40).grid(row=0, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(self, text="類型:").grid(row=1, column=0, sticky="e", padx=10, pady=6)
        init_type_key = str(initial.get("type", "clear_lines_if_column_empty"))
        self.var_type = tk.StringVar(value=rule_type_label(init_type_key))
        cb = ttk.Combobox(
            self, textvariable=self.var_type, width=37, state="readonly",
            values=list(RULE_TYPE_LABELS.values()),
        )
        cb.grid(row=1, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(self, text="欄位 (字母 A/B/C 或 0/1/2):").grid(row=2, column=0, sticky="e", padx=10, pady=6)
        self.var_col = tk.StringVar(value=str(initial.get("column", "")))
        ttk.Entry(self, textvariable=self.var_col, width=12).grid(row=2, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(self, text="行號 (用逗號分隔,如 374,375,376):").grid(row=3, column=0, sticky="e", padx=10, pady=6)
        self.var_lines = tk.StringVar(value=str(initial.get("lines", "")))
        ttk.Entry(self, textvariable=self.var_lines, width=40).grid(row=3, column=1, sticky="w", padx=6, pady=6)

        bar = ttk.Frame(self)
        bar.grid(row=4, column=0, columnspan=2, pady=10)
        ttk.Button(bar, text="確定", command=self._ok).pack(side="left", padx=4)
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="left", padx=4)

        self.wait_window()

    def _ok(self):
        name = self.var_name.get().strip()
        if not name:
            messagebox.showerror("驗證失敗", "名稱不可為空", parent=self)
            return
        rtype_label = self.var_type.get().strip()
        if not rtype_label:
            messagebox.showerror("驗證失敗", "類型不可為空", parent=self)
            return
        rtype = rule_type_key(rtype_label)
        try:
            col_to_index(self.var_col.get().strip())
        except Exception as e:
            messagebox.showerror("驗證失敗", f"欄位格式錯誤: {e}", parent=self)
            return
        try:
            lines = [int(x.strip()) for x in self.var_lines.get().split(",") if x.strip()]
            if not lines:
                raise ValueError
            for ln in lines:
                if ln < 1:
                    raise ValueError
        except ValueError:
            messagebox.showerror("驗證失敗", "行號必須是逗號分隔的 ≥1 整數", parent=self)
            return
        self.result = {
            "name": name,
            "type": rtype,
            "column": self.var_col.get().strip(),
            "lines": lines,
        }
        self.destroy()


# ---------- 使用說明內容 ----------

HELP_TEXT = [
    ("h1", "1. 匯出檔名規則"),
    ("p",  "用 {A} {B} {C}… 代表 CSV 對應欄位的值,其餘文字原樣保留。\n副檔名 .xml 會自動加上。同樣檔名的兩筆資料會被擋下不輸出。"),
    ("h2", "範例"),
    ("code", "{C}            →  C欄值.xml\n"
            "XML_{C}        →  XML_C欄值.xml\n"
            "{C}_{A}        →  C欄值_A欄值.xml\n"
            "114年_{C}_v1   →  114年_C欄值_v1.xml"),

    ("h1", "2. 欄位對應 (mappings)"),
    ("p",  "把 CSV 某欄的值套到範例 XML 指定行號。每條規則 4 個欄位:"),
    ("li", "欄位 (column):CSV 第幾欄,可填字母 A/B/C 或數字 0/1/2"),
    ("li", "行號 (line):範例 XML 的第幾行 (1-based)"),
    ("li", "標籤 (tag):輸出 XML 的標籤名,如 gco:CharacterString"),
    ("li", "備註 (note):純註解,只給人看"),
    ("h2", "套用方式"),
    ("p",  "直接把該行整行覆蓋成:"),
    ("code", "{原本縮排}<{tag}>{該欄值}</{tag}>"),
    ("note", "標籤只是輸出文字,不會檢查範本原本的標籤是否一致;範本若被重新格式化,所有行號都得重新對應。"),

    ("h1", "3. 特殊規則 (special_rules)"),
    ("p", "目前支援一種類型:「欄位為空時刪除指定行」"),
    ("li", "欄位:要檢查的 CSV 欄位"),
    ("li", "行號:多個用逗號分隔,例如 374,375,376"),
    ("p",  "當該欄位為空字串時,把這些行從輸出 XML 中整列刪除(不只清空,是真的整列拿掉)。"),
    ("note", "多條規則互不干擾:條件判斷都用範本「原始行號」,刪除集中在最後一次處理,不會因前面刪了行而導致後面行號錯位。"),

    ("h1", "4. 處理順序"),
    ("p", "每一筆 CSV 列的處理流程:"),
    ("li", "從乾淨的範本 XML 開始(每筆都從頭來)"),
    ("li", "套用所有「欄位對應」(mappings)"),
    ("li", "套用所有「特殊規則」(special_rules) — 收集要刪的行,一次刪掉"),
    ("li", "用「匯出檔名規則」算出檔名,寫成 .xml"),
    ("note", "mappings 寫到的內容若該行剛好被特殊規則刪除,內容也會一併消失。一般情況這就是你要的(該欄為空,所以連帶把對應段落整段砍掉)。"),

    ("h1", "5. CSV 格式要求"),
    ("li", "第一列必須是 header(會被跳過,只用來顯示欄位名稱)"),
    ("li", "編碼支援 UTF-8(有/無 BOM)與 CP950,自動嘗試"),
    ("li", "欄位空白會當作空字串處理"),

    ("h1", "6. 訊息符號"),
    ("li", "✓ 成功產生"),
    ("li", "✗ 失敗或檔名重複"),
    ("li", "略過 — 該列資料不完整或檔名為空"),
]


# ---------- 內嵌範例檔內容 (供「範例下載」分頁輸出) ----------

SAMPLE_CSV_TEXT = (
    "識別碼,名稱,圖號,Town,圖名,西W,東E,南S,北N\n"
    "95181001_114LU,國土利用現況調查_95181001_登知來(四),95181001,高雄市茂林區,登知來(四),120.75806,120.78306,22.97333,22.99833\n"
    "95181002_114LU,國土利用現況調查_95181002_登知來(一),95181002,高雄市桃源區,,120.78306,120.80806,22.97333,22.99833\n"
)

SAMPLE_XML_TEXT = """<?xml version="1.0" encoding="UTF-8"?>
<gmd:MD_Metadata xmlns:gmd="http://www.isotc211.org/2005/gmd" xmlns:gco="http://www.isotc211.org/2005/gco" xmlns:gml="http://www.opengis.net/gml" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.isotc211.org/2005/gmd http://www.isotc211.org/2005/gmd/metadataEntity.xsd">
  <!--@twsmp:{"description":"","excludedChildren":[],"hint":"","mustHandleChildren":[],"questionInfo":null,"template":-1}-->
  <gmd:fileIdentifier>
    <gco:CharacterString>96201001_114LU</gco:CharacterString>
  </gmd:fileIdentifier>
  <gmd:language>
    <gco:CharacterString>chi</gco:CharacterString>
  </gmd:language>
  <gmd:characterSet>
    <gmd:MD_CharacterSetCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#MD_CharacterSetCode" codeListValue="utf8" codeSpace="ISOTC211/19115">utf8</gmd:MD_CharacterSetCode>
  </gmd:characterSet>
  <gmd:hierarchyLevel>
    <gmd:MD_ScopeCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#MD_ScopeCode" codeListValue="dataset" codeSpace="ISOTC211/19115">dataset</gmd:MD_ScopeCode>
  </gmd:hierarchyLevel>
  <gmd:contact xlink:type="simple">
    <gmd:CI_ResponsibleParty>
      <gmd:organisationName>
        <gco:CharacterString>內政部國土測繪中心基本圖資測製科</gco:CharacterString>
      </gmd:organisationName>
      <gmd:contactInfo xlink:type="simple">
        <gmd:CI_Contact>
          <gmd:phone xlink:type="simple">
            <gmd:CI_Telephone>
              <gmd:voice>
                <gco:CharacterString>+886-4-22522966</gco:CharacterString>
              </gmd:voice>
              <gmd:facsimile>
                <gco:CharacterString>+886-4-22592273</gco:CharacterString>
              </gmd:facsimile>
            </gmd:CI_Telephone>
          </gmd:phone>
          <gmd:address xlink:type="simple">
            <gmd:CI_Address>
              <gmd:deliveryPoint>
                <gco:CharacterString>黎明路二段４９７號四樓</gco:CharacterString>
              </gmd:deliveryPoint>
              <gmd:city>
                <gco:CharacterString>臺中市南屯區</gco:CharacterString>
              </gmd:city>
              <gmd:postalCode>
                <gco:CharacterString>408281</gco:CharacterString>
              </gmd:postalCode>
              <gmd:country>
                <gco:CharacterString>中華民國</gco:CharacterString>
              </gmd:country>
              <gmd:electronicMailAddress>
                <gco:CharacterString>m3@mail.nlsc.gov.tw</gco:CharacterString>
              </gmd:electronicMailAddress>
            </gmd:CI_Address>
          </gmd:address>
        </gmd:CI_Contact>
      </gmd:contactInfo>
      <gmd:role>
        <gmd:CI_RoleCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#CI_RoleCode" codeListValue="custodian" codeSpace="ISOTC211/19115">custodian</gmd:CI_RoleCode>
      </gmd:role>
    </gmd:CI_ResponsibleParty>
  </gmd:contact>
  <gmd:contact xlink:type="simple">
    <gmd:CI_ResponsibleParty>
      <gmd:organisationName>
        <gco:CharacterString>內政部國土測繪中心圖資供應管理科</gco:CharacterString>
      </gmd:organisationName>
      <gmd:contactInfo xlink:type="simple">
        <gmd:CI_Contact>
          <gmd:phone xlink:type="simple">
            <gmd:CI_Telephone>
              <gmd:voice>
                <gco:CharacterString>+886-4-22522966</gco:CharacterString>
              </gmd:voice>
              <gmd:facsimile>
                <gco:CharacterString>+886-4-22514536</gco:CharacterString>
              </gmd:facsimile>
            </gmd:CI_Telephone>
          </gmd:phone>
          <gmd:address xlink:type="simple">
            <gmd:CI_Address>
              <gmd:deliveryPoint>
                <gco:CharacterString>黎明路二段４９７號四樓</gco:CharacterString>
              </gmd:deliveryPoint>
              <gmd:city>
                <gco:CharacterString>臺中市南屯區</gco:CharacterString>
              </gmd:city>
              <gmd:postalCode>
                <gco:CharacterString>408281</gco:CharacterString>
              </gmd:postalCode>
              <gmd:country>
                <gco:CharacterString>中華民國</gco:CharacterString>
              </gmd:country>
              <gmd:electronicMailAddress>
                <gco:CharacterString>m5@mail.nlsc.gov.tw</gco:CharacterString>
              </gmd:electronicMailAddress>
            </gmd:CI_Address>
          </gmd:address>
        </gmd:CI_Contact>
      </gmd:contactInfo>
      <gmd:role>
        <gmd:CI_RoleCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#CI_RoleCode" codeListValue="distributor" codeSpace="ISOTC211/19115">distributor</gmd:CI_RoleCode>
      </gmd:role>
    </gmd:CI_ResponsibleParty>
  </gmd:contact>
  <gmd:contact xlink:type="simple">
    <gmd:CI_ResponsibleParty>
      <gmd:organisationName>
        <gco:CharacterString>群立科技股份有限公司</gco:CharacterString>
      </gmd:organisationName>
      <gmd:contactInfo xlink:type="simple">
        <gmd:CI_Contact>
          <gmd:phone xlink:type="simple">
            <gmd:CI_Telephone>
              <gmd:voice>
                <gco:CharacterString>+886-4-27078899</gco:CharacterString>
              </gmd:voice>
              <gmd:facsimile>
                <gco:CharacterString>+886-4-27062899</gco:CharacterString>
              </gmd:facsimile>
            </gmd:CI_Telephone>
          </gmd:phone>
          <gmd:address xlink:type="simple">
            <gmd:CI_Address>
              <gmd:deliveryPoint>
                <gco:CharacterString>臺灣大道三段５４０號七樓之５</gco:CharacterString>
              </gmd:deliveryPoint>
              <gmd:city>
                <gco:CharacterString>臺中市西屯區</gco:CharacterString>
              </gmd:city>
              <gmd:postalCode>
                <gco:CharacterString>407603</gco:CharacterString>
              </gmd:postalCode>
              <gmd:country>
                <gco:CharacterString>中華民國</gco:CharacterString>
              </gmd:country>
              <gmd:electronicMailAddress>
                <gco:CharacterString>service@geoforce.com.tw</gco:CharacterString>
              </gmd:electronicMailAddress>
            </gmd:CI_Address>
          </gmd:address>
        </gmd:CI_Contact>
      </gmd:contactInfo>
      <gmd:role>
        <gmd:CI_RoleCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#CI_RoleCode" codeListValue="originator" codeSpace="ISOTC211/19115">originator</gmd:CI_RoleCode>
      </gmd:role>
    </gmd:CI_ResponsibleParty>
  </gmd:contact>
  <gmd:dateStamp>
    <gco:Date>2025-11-28</gco:Date>
  </gmd:dateStamp>
  <gmd:metadataStandardName>
    <gco:CharacterString>TWSMP</gco:CharacterString>
  </gmd:metadataStandardName>
  <gmd:metadataStandardVersion>
    <gco:CharacterString>2.0</gco:CharacterString>
  </gmd:metadataStandardVersion>
  <gmd:spatialRepresentationInfo xlink:type="simple">
    <gmd:MD_VectorSpatialRepresentation>
      <gmd:geometricObjects xlink:type="simple">
        <gmd:MD_GeometricObjects>
          <gmd:geometricObjectType>
            <gmd:MD_GeometricObjectTypeCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#MD_GeometricObjectTypeCode" codeListValue="surface" codeSpace="ISOTC211/19115">surface</gmd:MD_GeometricObjectTypeCode>
          </gmd:geometricObjectType>
        </gmd:MD_GeometricObjects>
      </gmd:geometricObjects>
    </gmd:MD_VectorSpatialRepresentation>
  </gmd:spatialRepresentationInfo>
  <gmd:referenceSystemInfo xlink:type="simple">
    <gmd:MD_ReferenceSystem>
      <gmd:referenceSystemIdentifier xlink:type="simple">
        <gmd:RS_Identifier>
          <gmd:code>
            <gco:CharacterString>TWD97(121分帶)</gco:CharacterString>
          </gmd:code>
        </gmd:RS_Identifier>
      </gmd:referenceSystemIdentifier>
    </gmd:MD_ReferenceSystem>
  </gmd:referenceSystemInfo>
  <gmd:identificationInfo xlink:type="simple">
    <gmd:MD_DataIdentification>
      <gmd:citation xlink:type="simple">
        <gmd:CI_Citation>
          <gmd:title>
            <gco:CharacterString>國土利用現況調查_96201001_能高山(四)</gco:CharacterString>
          </gmd:title>
          <gmd:date xlink:type="simple">
            <gmd:CI_Date>
              <gmd:date>
                <gco:Date>2025-11-28</gco:Date>
              </gmd:date>
              <gmd:dateType>
                <gmd:CI_DateTypeCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#CI_DateTypeCode" codeListValue="creation" codeSpace="ISOTC211/19115">creation</gmd:CI_DateTypeCode>
              </gmd:dateType>
            </gmd:CI_Date>
          </gmd:date>
          <gmd:date xlink:type="simple">
            <gmd:CI_Date>
              <gmd:date>
                <gco:Date>1900-01-01</gco:Date>
              </gmd:date>
              <gmd:dateType>
                <gmd:CI_DateTypeCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#CI_DateTypeCode" codeListValue="revision" codeSpace="ISOTC211/19115">revision</gmd:CI_DateTypeCode>
              </gmd:dateType>
            </gmd:CI_Date>
          </gmd:date>
          <gmd:date xlink:type="simple">
            <gmd:CI_Date>
              <gmd:date>
                <gco:Date>1900-01-01</gco:Date>
              </gmd:date>
              <gmd:dateType>
                <gmd:CI_DateTypeCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#CI_DateTypeCode" codeListValue="publication" codeSpace="ISOTC211/19115">publication</gmd:CI_DateTypeCode>
              </gmd:dateType>
            </gmd:CI_Date>
          </gmd:date>
        </gmd:CI_Citation>
      </gmd:citation>
      <gmd:abstract>
        <gco:CharacterString>內政部國土測繪中心委託群立科技股份有限公司運用航遙測影像內涵豐富資訊，搭配GIS輔助資料及地面調查作業，快速、確實得獲取國土利用現況調查成果。</gco:CharacterString>
      </gmd:abstract>
      <gmd:purpose>
        <gco:CharacterString>隨著全球經濟的蓬勃發展，國內已由農業轉變為工商服務業發展並進的型態，土地利用的變化加快，原有資料已不敷使用；因此，內政部交由國土測繪中心規劃由測量隊自辦及委請廠商運用航遙測影像內涵豐富資訊，搭配GIS輔助資料及地面調查作業，獲取國土利用現況調查成果，提供國土規劃及各項國家政策推動所需基礎資料。</gco:CharacterString>
      </gmd:purpose>
      <gmd:status>
        <gmd:MD_ProgressCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#MD_ProgressCode" codeListValue="completed" codeSpace="ISOTC211/19115">completed</gmd:MD_ProgressCode>
      </gmd:status>
      <gmd:pointOfContact xlink:type="simple">
        <gmd:CI_ResponsibleParty>
          <gmd:organisationName>
            <gco:CharacterString>內政部國土測繪中心基本圖資測製科</gco:CharacterString>
          </gmd:organisationName>
          <gmd:contactInfo xlink:type="simple">
            <gmd:CI_Contact>
              <gmd:phone xlink:type="simple">
                <gmd:CI_Telephone>
                  <gmd:voice>
                    <gco:CharacterString>+886-4-22522966</gco:CharacterString>
                  </gmd:voice>
                  <gmd:facsimile>
                    <gco:CharacterString>+886-4-22592273</gco:CharacterString>
                  </gmd:facsimile>
                </gmd:CI_Telephone>
              </gmd:phone>
              <gmd:address xlink:type="simple">
                <gmd:CI_Address>
                  <gmd:deliveryPoint>
                    <gco:CharacterString>黎明路二段４９７號四樓</gco:CharacterString>
                  </gmd:deliveryPoint>
                  <gmd:city>
                    <gco:CharacterString>臺中市南屯區</gco:CharacterString>
                  </gmd:city>
                  <gmd:postalCode>
                    <gco:CharacterString>408281</gco:CharacterString>
                  </gmd:postalCode>
                  <gmd:country>
                    <gco:CharacterString>中華民國</gco:CharacterString>
                  </gmd:country>
                  <gmd:electronicMailAddress>
                    <gco:CharacterString>m3@mail.nlsc.gov.tw</gco:CharacterString>
                  </gmd:electronicMailAddress>
                </gmd:CI_Address>
              </gmd:address>
            </gmd:CI_Contact>
          </gmd:contactInfo>
          <gmd:role>
            <gmd:CI_RoleCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#CI_RoleCode" codeListValue="custodian" codeSpace="ISOTC211/19115">custodian</gmd:CI_RoleCode>
          </gmd:role>
        </gmd:CI_ResponsibleParty>
      </gmd:pointOfContact>
      <gmd:pointOfContact xlink:type="simple">
        <gmd:CI_ResponsibleParty>
          <gmd:organisationName>
            <gco:CharacterString>內政部國土測繪中心圖資供應管理科</gco:CharacterString>
          </gmd:organisationName>
          <gmd:contactInfo xlink:type="simple">
            <gmd:CI_Contact>
              <gmd:phone xlink:type="simple">
                <gmd:CI_Telephone>
                  <gmd:voice>
                    <gco:CharacterString>+886-4-22522966</gco:CharacterString>
                  </gmd:voice>
                  <gmd:facsimile>
                    <gco:CharacterString>+886-4-22514536</gco:CharacterString>
                  </gmd:facsimile>
                </gmd:CI_Telephone>
              </gmd:phone>
              <gmd:address xlink:type="simple">
                <gmd:CI_Address>
                  <gmd:deliveryPoint>
                    <gco:CharacterString>黎明路二段４９７號四樓</gco:CharacterString>
                  </gmd:deliveryPoint>
                  <gmd:city>
                    <gco:CharacterString>臺中市南屯區</gco:CharacterString>
                  </gmd:city>
                  <gmd:postalCode>
                    <gco:CharacterString>408281</gco:CharacterString>
                  </gmd:postalCode>
                  <gmd:country>
                    <gco:CharacterString>中華民國</gco:CharacterString>
                  </gmd:country>
                  <gmd:electronicMailAddress>
                    <gco:CharacterString>m5@mail.nlsc.gov.tw</gco:CharacterString>
                  </gmd:electronicMailAddress>
                </gmd:CI_Address>
              </gmd:address>
            </gmd:CI_Contact>
          </gmd:contactInfo>
          <gmd:role>
            <gmd:CI_RoleCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#CI_RoleCode" codeListValue="distributor" codeSpace="ISOTC211/19115">distributor</gmd:CI_RoleCode>
          </gmd:role>
        </gmd:CI_ResponsibleParty>
      </gmd:pointOfContact>
      <gmd:pointOfContact xlink:type="simple">
        <gmd:CI_ResponsibleParty>
          <gmd:organisationName>
            <gco:CharacterString>群立科技股份有限公司</gco:CharacterString>
          </gmd:organisationName>
          <gmd:contactInfo xlink:type="simple">
            <gmd:CI_Contact>
              <gmd:phone xlink:type="simple">
                <gmd:CI_Telephone>
                  <gmd:voice>
                    <gco:CharacterString>+886-4-27078899</gco:CharacterString>
                  </gmd:voice>
                  <gmd:facsimile>
                    <gco:CharacterString>+886-4-27062899</gco:CharacterString>
                  </gmd:facsimile>
                </gmd:CI_Telephone>
              </gmd:phone>
              <gmd:address xlink:type="simple">
                <gmd:CI_Address>
                  <gmd:deliveryPoint>
                    <gco:CharacterString>臺灣大道三段５４０號七樓之５</gco:CharacterString>
                  </gmd:deliveryPoint>
                  <gmd:city>
                    <gco:CharacterString>臺中市西屯區</gco:CharacterString>
                  </gmd:city>
                  <gmd:postalCode>
                    <gco:CharacterString>407603</gco:CharacterString>
                  </gmd:postalCode>
                  <gmd:country>
                    <gco:CharacterString>中華民國</gco:CharacterString>
                  </gmd:country>
                  <gmd:electronicMailAddress>
                    <gco:CharacterString>service@geoforce.com.tw</gco:CharacterString>
                  </gmd:electronicMailAddress>
                </gmd:CI_Address>
              </gmd:address>
            </gmd:CI_Contact>
          </gmd:contactInfo>
          <gmd:role>
            <gmd:CI_RoleCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#CI_RoleCode" codeListValue="originator" codeSpace="ISOTC211/19115">originator</gmd:CI_RoleCode>
          </gmd:role>
        </gmd:CI_ResponsibleParty>
      </gmd:pointOfContact>
      <gmd:resourceMaintenance xlink:type="simple">
        <gmd:MD_MaintenanceInformation>
          <gmd:maintenanceAndUpdateFrequency>
            <gmd:MD_MaintenanceFrequencyCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#MD_MaintenanceFrequencyCode" codeListValue="irregular" codeSpace="ISOTC211/19115">irregular</gmd:MD_MaintenanceFrequencyCode>
          </gmd:maintenanceAndUpdateFrequency>
        </gmd:MD_MaintenanceInformation>
      </gmd:resourceMaintenance>
      <gmd:descriptiveKeywords xlink:type="simple">
        <gmd:MD_Keywords>
          <gmd:keyword>
            <gco:CharacterString>國土利用現況調查</gco:CharacterString>
          </gmd:keyword>
          <gmd:keyword>
            <gco:CharacterString>96201001</gco:CharacterString>
          </gmd:keyword>
          <gmd:keyword>
            <gco:CharacterString>114年度國土利用現況調查成果更新</gco:CharacterString>
          </gmd:keyword>
          <gmd:keyword>
            <gco:CharacterString>南投縣</gco:CharacterString>
          </gmd:keyword>
          <gmd:keyword>
            <gco:CharacterString>高山</gco:CharacterString>
          </gmd:keyword>
          <gmd:keyword>
            <gco:CharacterString>國土利用現況調查成果</gco:CharacterString>
          </gmd:keyword>
          <gmd:keyword>
            <gco:CharacterString>土地利用</gco:CharacterString>
          </gmd:keyword>
          <gmd:keyword>
            <gco:CharacterString>國土利用</gco:CharacterString>
          </gmd:keyword>
          <gmd:keyword>
            <gco:CharacterString>五千分之一</gco:CharacterString>
          </gmd:keyword>
          <gmd:keyword>
            <gco:CharacterString>地理資訊系統</gco:CharacterString>
          </gmd:keyword>
          <gmd:keyword>
            <gco:CharacterString>GIS</gco:CharacterString>
          </gmd:keyword>
          <gmd:keyword>
            <gco:CharacterString>數值資料檔</gco:CharacterString>
          </gmd:keyword>
        </gmd:MD_Keywords>
      </gmd:descriptiveKeywords>
      <gmd:resourceConstraints xlink:type="simple">
        <gmd:MD_LegalConstraints>
          <gmd:accessConstraints>
            <gmd:MD_RestrictionCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#MD_RestrictionCode" codeListValue="trademark" codeSpace="ISOTC211/19115">trademark</gmd:MD_RestrictionCode>
          </gmd:accessConstraints>
          <gmd:useConstraints>
            <gmd:MD_RestrictionCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#MD_RestrictionCode" codeListValue="trademark" codeSpace="ISOTC211/19115">trademark</gmd:MD_RestrictionCode>
          </gmd:useConstraints>
          <gmd:otherConstraints>
            <gco:CharacterString>1.數值資料檔僅賦予使用權，申請單位非經內政部國土測繪中心同意，不得自行轉錄、轉售、贈與、租賃或質押，亦不得以附加或改良資料為由，作為任何其他商業用途。2.數值資料檔須由專人保管，不得任意移交、複製。非經國防部同意，不得攜出國外。3.申請單位應遵照「國家機密保護法」、「著作權法」與「行政機關電子資料流通實施要點」及其他相關法令規定使用數值資料檔。4.內政部國土測繪中心同仁自由瀏覽/申請下載;內政部（地政司）同仁自由瀏覽/申請下載;其他政府單位同仁自由瀏覽/須申購;其他學術單位同仁自由瀏覽/須申購;一般民眾自由瀏覽/須申購;民間企業自由瀏覽/須申購。</gco:CharacterString>
          </gmd:otherConstraints>
        </gmd:MD_LegalConstraints>
      </gmd:resourceConstraints>
      <gmd:resourceConstraints xlink:type="simple">
        <gmd:MD_SecurityConstraints>
          <gmd:classification>
            <gmd:MD_ClassificationCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#MD_ClassificationCode" codeListValue="unclassified" codeSpace="ISOTC211/19115">unclassified</gmd:MD_ClassificationCode>
          </gmd:classification>
        </gmd:MD_SecurityConstraints>
      </gmd:resourceConstraints>
      <gmd:spatialRepresentationType>
        <gmd:MD_SpatialRepresentationTypeCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#MD_SpatialRepresentationTypeCode" codeListValue="vector" codeSpace="ISOTC211/19115">vector</gmd:MD_SpatialRepresentationTypeCode>
      </gmd:spatialRepresentationType>
      <gmd:spatialResolution>
        <gmd:MD_Resolution>
          <gmd:equivalentScale xlink:type="simple">
            <gmd:MD_RepresentativeFraction>
              <gmd:denominator>
                <gco:Integer>5000</gco:Integer>
              </gmd:denominator>
            </gmd:MD_RepresentativeFraction>
          </gmd:equivalentScale>
        </gmd:MD_Resolution>
      </gmd:spatialResolution>
      <gmd:language>
        <gco:CharacterString>chi</gco:CharacterString>
      </gmd:language>
      <gmd:characterSet>
        <gmd:MD_CharacterSetCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#MD_CharacterSetCode" codeListValue="utf8" codeSpace="ISOTC211/19115">utf8</gmd:MD_CharacterSetCode>
      </gmd:characterSet>
      <gmd:topicCategory>
        <gmd:MD_TopicCategoryCode>imageryBaseMapsEarthCover</gmd:MD_TopicCategoryCode>
      </gmd:topicCategory>
      <gmd:extent xlink:type="simple">
        <gmd:EX_Extent>
          <gmd:geographicElement xlink:type="simple">
            <gmd:EX_GeographicBoundingBox>
              <gmd:westBoundLongitude>
                <gco:Decimal>121</gco:Decimal>
              </gmd:westBoundLongitude>
              <gmd:eastBoundLongitude>
                <gco:Decimal>121</gco:Decimal>
              </gmd:eastBoundLongitude>
              <gmd:southBoundLatitude>
                <gco:Decimal>23</gco:Decimal>
              </gmd:southBoundLatitude>
              <gmd:northBoundLatitude>
                <gco:Decimal>23</gco:Decimal>
              </gmd:northBoundLatitude>
            </gmd:EX_GeographicBoundingBox>
          </gmd:geographicElement>
        </gmd:EX_Extent>
      </gmd:extent>
    </gmd:MD_DataIdentification>
  </gmd:identificationInfo>
  <gmd:distributionInfo xlink:type="simple">
    <gmd:MD_Distribution>
      <gmd:distributionFormat xlink:type="simple">
        <gmd:MD_Format>
          <gmd:name>
            <gco:CharacterString>電子資料_SHP</gco:CharacterString>
          </gmd:name>
          <gmd:version>
            <gco:CharacterString>SHP</gco:CharacterString>
          </gmd:version>
        </gmd:MD_Format>
      </gmd:distributionFormat>
      <gmd:distributionFormat xlink:type="simple">
        <gmd:MD_Format>
          <gmd:name>
            <gco:CharacterString>電子資料_GML</gco:CharacterString>
          </gmd:name>
          <gmd:version>
            <gco:CharacterString>GML 3.2</gco:CharacterString>
          </gmd:version>
        </gmd:MD_Format>
      </gmd:distributionFormat>
      <gmd:distributor xlink:type="simple">
        <gmd:MD_Distributor>
          <gmd:distributorContact xlink:type="simple">
            <gmd:CI_ResponsibleParty>
              <gmd:organisationName>
                <gco:CharacterString>內政部國土測繪中心圖資供應管理科</gco:CharacterString>
              </gmd:organisationName>
              <gmd:contactInfo xlink:type="simple">
                <gmd:CI_Contact>
                  <gmd:phone xlink:type="simple">
                    <gmd:CI_Telephone>
                      <gmd:voice>
                        <gco:CharacterString>+886-4-22522966</gco:CharacterString>
                      </gmd:voice>
                      <gmd:facsimile>
                        <gco:CharacterString>+886-4-22514536</gco:CharacterString>
                      </gmd:facsimile>
                    </gmd:CI_Telephone>
                  </gmd:phone>
                  <gmd:address xlink:type="simple">
                    <gmd:CI_Address>
                      <gmd:deliveryPoint>
                        <gco:CharacterString>黎明路二段４９７號四樓</gco:CharacterString>
                      </gmd:deliveryPoint>
                      <gmd:city>
                        <gco:CharacterString>臺中市南屯區</gco:CharacterString>
                      </gmd:city>
                      <gmd:postalCode>
                        <gco:CharacterString>408281</gco:CharacterString>
                      </gmd:postalCode>
                      <gmd:country>
                        <gco:CharacterString>中華民國</gco:CharacterString>
                      </gmd:country>
                      <gmd:electronicMailAddress>
                        <gco:CharacterString>m5@mail.nlsc.gov.tw</gco:CharacterString>
                      </gmd:electronicMailAddress>
                    </gmd:CI_Address>
                  </gmd:address>
                  <gmd:onlineResource xlink:type="simple">
                    <gmd:CI_OnlineResource>
                      <gmd:linkage>
                        <gmd:URL>https://whgis-nlsc.moi.gov.tw/</gmd:URL>
                      </gmd:linkage>
                    </gmd:CI_OnlineResource>
                  </gmd:onlineResource>
                </gmd:CI_Contact>
              </gmd:contactInfo>
              <gmd:role>
                <gmd:CI_RoleCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#CI_RoleCode" codeListValue="distributor" codeSpace="ISOTC211/19115">distributor</gmd:CI_RoleCode>
              </gmd:role>
            </gmd:CI_ResponsibleParty>
          </gmd:distributorContact>
          <gmd:distributionOrderProcess xlink:type="simple">
            <gmd:MD_StandardOrderProcess>
              <gmd:fees>
                <gco:CharacterString>需收費</gco:CharacterString>
              </gmd:fees>
              <gmd:orderingInstructions>
                <gco:CharacterString>依「國土測繪成果資料收費標準」及「內政部國土測繪中心測繪成果電子資料流通作業要點」規定辦理，或參考內政部國土測繪中心全球資訊網https://www.nlsc.gov.tw/-便民服務-測繪圖資供應之相關資訊。</gco:CharacterString>
              </gmd:orderingInstructions>
            </gmd:MD_StandardOrderProcess>
          </gmd:distributionOrderProcess>
        </gmd:MD_Distributor>
      </gmd:distributor>
    </gmd:MD_Distribution>
  </gmd:distributionInfo>
  <gmd:dataQualityInfo xlink:type="simple">
    <gmd:DQ_DataQuality>
      <gmd:scope xlink:type="simple">
        <gmd:DQ_Scope>
          <gmd:level>
            <gmd:MD_ScopeCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#MD_ScopeCode" codeListValue="dataset" codeSpace="ISOTC211/19115">dataset</gmd:MD_ScopeCode>
          </gmd:level>
        </gmd:DQ_Scope>
      </gmd:scope>
      <gmd:report xlink:type="simple">
        <gmd:DQ_CompletenessCommission>
          <gmd:nameOfMeasure>
            <gco:CharacterString>國土利用現況調查成果檢查</gco:CharacterString>
          </gmd:nameOfMeasure>
          <gmd:measureDescription>
            <gco:CharacterString>成果檢查方法為現場外業抽樣檢核及內業以IMAP程式對數值成果做全面性檢核。1.現場外業抽樣檢查數量為辦理圖幅數量之10%，檢查合格通過率為90%，檢查分類內容正確性。2.內業檢核為檢查數值成果屬性資料欄位完整性及分類內容正確性。3.查對數值成果及詮釋資料之種類、數量及品質。</gco:CharacterString>
          </gmd:measureDescription>
          <gmd:result xlink:type="simple">
            <gmd:DQ_ConformanceResult>
              <gmd:specification xlink:type="simple">
                <gmd:CI_Citation>
                  <gmd:title>
                    <gco:CharacterString>國土利用現況調查成果檢查作業說明</gco:CharacterString>
                  </gmd:title>
                  <gmd:date xlink:type="simple">
                    <gmd:CI_Date>
                      <gmd:date>
                        <gco:Date>2024-02-23</gco:Date>
                      </gmd:date>
                      <gmd:dateType>
                        <gmd:CI_DateTypeCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#CI_DateTypeCode" codeListValue="publication" codeSpace="ISOTC211/19115">publication</gmd:CI_DateTypeCode>
                      </gmd:dateType>
                    </gmd:CI_Date>
                  </gmd:date>
                </gmd:CI_Citation>
              </gmd:specification>
              <gmd:explanation>
                <gco:CharacterString>本成果業經查核通過，符合國土利用現況調查成果相關規範之要求。</gco:CharacterString>
              </gmd:explanation>
              <gmd:pass>
                <gco:Boolean>true</gco:Boolean>
              </gmd:pass>
            </gmd:DQ_ConformanceResult>
          </gmd:result>
        </gmd:DQ_CompletenessCommission>
      </gmd:report>
      <gmd:report xlink:type="simple">
        <gmd:DQ_AbsoluteExternalPositionalAccuracy>
          <gmd:nameOfMeasure>
            <gco:CharacterString>國土利用現況調查成果檢查</gco:CharacterString>
          </gmd:nameOfMeasure>
          <gmd:measureDescription>
            <gco:CharacterString>1.圖幅四鄰接邊確實。2.所有圖元屬性依規定設定。3.面狀資料封閉填滿。4.圖形資料分類原則符合108年版土地利用分級分類系統表（陸域部分）。5.圖形資料具多邊形與位相關係資料。</gco:CharacterString>
          </gmd:measureDescription>
          <gmd:result xlink:type="simple">
            <gmd:DQ_ConformanceResult>
              <gmd:specification xlink:type="simple">
                <gmd:CI_Citation>
                  <gmd:title>
                    <gco:CharacterString>國土利用現況調查成果檢查作業說明</gco:CharacterString>
                  </gmd:title>
                  <gmd:date xlink:type="simple">
                    <gmd:CI_Date>
                      <gmd:date>
                        <gco:Date>2024-02-23</gco:Date>
                      </gmd:date>
                      <gmd:dateType>
                        <gmd:CI_DateTypeCode codeList="http://www.isotc211.org/2005/resources/codeList.xml#CI_DateTypeCode" codeListValue="publication" codeSpace="ISOTC211/19115">publication</gmd:CI_DateTypeCode>
                      </gmd:dateType>
                    </gmd:CI_Date>
                  </gmd:date>
                </gmd:CI_Citation>
              </gmd:specification>
              <gmd:explanation>
                <gco:CharacterString>本成果業經查核通過，符合國土利用現況調查成果相關規範之要求。</gco:CharacterString>
              </gmd:explanation>
              <gmd:pass>
                <gco:Boolean>true</gco:Boolean>
              </gmd:pass>
            </gmd:DQ_ConformanceResult>
          </gmd:result>
        </gmd:DQ_AbsoluteExternalPositionalAccuracy>
      </gmd:report>
      <gmd:lineage xlink:type="simple">
        <gmd:LI_Lineage>
          <gmd:statement>
            <gco:CharacterString>1.本資料成果係以外業調查及內業編輯數值成果等方式進行作業，所參考影像資料為地面解析度0.15公尺~1.50公尺之航照正射影像或衛星正射影像。作業時參考正射影像、地籍圖及臺灣通用電子地圖等圖資為調查底圖，並配合現場調查對土地利用情形進行分類。2.本資料部分成果係引用其他單位調查成果，並轉換對應至108年版土地利用分級分類系統表（陸域部分）。</gco:CharacterString>
          </gmd:statement>
        </gmd:LI_Lineage>
      </gmd:lineage>
    </gmd:DQ_DataQuality>
  </gmd:dataQualityInfo>
</gmd:MD_Metadata>
"""


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
