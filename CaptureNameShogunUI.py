# coding: utf-8
"""CaptureNameShogun (UI版)

CSVに並べたキャプチャ名を1件ずつ選び、Shogun Live のキャプチャ名として設定する。
元の CaptureNameShogun.py と同じ機能を、見た目と操作性を改善した版。

キーボード:
    ↑ / ←     前の名前
    ↓ / →     次の名前
    Enter     選択中の名前をShogun Liveに設定
    Ctrl+O    CSVを開く
    F5        CSVを再読み込み
"""

import csv
import os
import unicodedata

import tkinter as tk
from tkinter import filedialog

try:
    from vicon_core_api import Client
    from shogun_live_api import CaptureServices
    HAS_SDK = True
except ImportError:  # SDKが無い環境でもUIの確認だけは出来るようにする
    Client = None
    CaptureServices = None
    HAS_SDK = False


# --- 見た目の設定 (ShogunControl_v7.py と同じ Catppuccin Mocha 系) ---------
BG = "#1E1E2E"
SURFACE = "#181825"
CARD = "#313244"
BUTTON = "#45475A"
BUTTON_HOVER = "#585B70"
TEXT = "#CDD6F4"
MUTED = "#6C7086"
ACCENT = "#89DCEB"
SUCCESS = "#A6E3A1"
ERROR = "#F38BA8"
INFO = "#89B4FA"
WARNING = "#FAB387"

FONT = "Yu Gothic UI"

DEFAULT_CSV = "20260716.csv"
HOST = "localhost"


def is_ascii_safe(text):
    """全角・記号・空白を含まない（キャプチャ名として使える）なら True。"""
    for ch in text:
        if unicodedata.east_asian_width(ch) in ("F", "A", "W") or ch == " ":
            return False
    return True


class HoverButton(tk.Button):
    """ホバーで色が変わるボタン。"""

    def __init__(self, master, bg=BUTTON, hover=BUTTON_HOVER, **kwargs):
        super().__init__(
            master,
            bg=bg,
            activebackground=hover,
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            **kwargs
        )
        self._bg = bg
        self._hover = hover
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")

    def _on_enter(self, _event):
        if str(self["state"]) != "disabled":
            self.configure(bg=self._hover)

    def _on_leave(self, _event):
        self.configure(bg=self._bg)


class CaptureNameApp:
    def __init__(self, root):
        self.root = root
        self.names = []
        self.invalid = []          # [(行番号, 名前), ...]
        self.index = 0
        self.csv_path = ""
        self.capture = None

        self._build_ui()
        self._connect()
        self._load_initial_csv()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        r = self.root
        r.title("Capture Name - Shogun Live")
        r.geometry("560x620")
        r.minsize(480, 540)
        r.configure(bg=BG)

        # ヘッダー
        header = tk.Frame(r, bg=SURFACE)
        header.pack(fill="x")
        tk.Label(
            header, text="Capture Name", font=(FONT, 18, "bold"),
            bg=SURFACE, fg=ACCENT
        ).pack(side="left", padx=20, pady=14)

        self.conn_var = tk.StringVar(value="● 未接続")
        self.conn_label = tk.Label(
            header, textvariable=self.conn_var, font=(FONT, 10, "bold"),
            bg=SURFACE, fg=MUTED
        )
        self.conn_label.pack(side="right", padx=20)

        body = tk.Frame(r, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=16)

        # CSV選択行
        filebar = tk.Frame(body, bg=BG)
        filebar.pack(fill="x")
        tk.Label(
            filebar, text="CSV", font=(FONT, 10, "bold"), bg=BG, fg=MUTED
        ).pack(side="left")
        self.file_var = tk.StringVar(value="(未選択)")
        tk.Label(
            filebar, textvariable=self.file_var, font=(FONT, 10),
            bg=BG, fg=TEXT, anchor="w"
        ).pack(side="left", padx=8, fill="x", expand=True)
        HoverButton(
            filebar, text="再読込", font=(FONT, 9), fg=TEXT,
            padx=10, pady=3, command=self.reload_csv
        ).pack(side="right", padx=(6, 0))
        HoverButton(
            filebar, text="参照...", font=(FONT, 9), fg=TEXT,
            padx=10, pady=3, command=self.browse_csv
        ).pack(side="right")

        # 現在の名前カード
        card = tk.Frame(body, bg=CARD, highlightthickness=0)
        card.pack(fill="x", pady=(16, 0))

        self.count_var = tk.StringVar(value="0 / 0")
        tk.Label(
            card, textvariable=self.count_var, font=(FONT, 10),
            bg=CARD, fg=MUTED
        ).pack(anchor="e", padx=14, pady=(10, 0))

        self.name_var = tk.StringVar(value="—")
        self.name_label = tk.Label(
            card, textvariable=self.name_var, font=(FONT, 22, "bold"),
            bg=CARD, fg=TEXT, wraplength=460, justify="center"
        )
        self.name_label.pack(padx=14, pady=(2, 6))

        self.note_var = tk.StringVar(value="")
        self.note_label = tk.Label(
            card, textvariable=self.note_var, font=(FONT, 10),
            bg=CARD, fg=WARNING
        )
        self.note_label.pack(padx=14, pady=(0, 12))

        # 操作ボタン
        nav = tk.Frame(body, bg=BG)
        nav.pack(fill="x", pady=14)
        nav.columnconfigure(0, weight=1)
        nav.columnconfigure(1, weight=2)
        nav.columnconfigure(2, weight=1)

        self.prev_btn = HoverButton(
            nav, text="❮  前", font=(FONT, 12, "bold"), fg=TEXT,
            pady=10, command=self.prev_name
        )
        self.prev_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.set_btn = HoverButton(
            nav, text="この名前を設定", font=(FONT, 12, "bold"),
            bg=INFO, hover=ACCENT, fg=SURFACE, pady=10, command=self.apply_name
        )
        self.set_btn.grid(row=0, column=1, sticky="ew", padx=6)

        self.next_btn = HoverButton(
            nav, text="次  ❯", font=(FONT, 12, "bold"), fg=TEXT,
            pady=10, command=self.next_name
        )
        self.next_btn.grid(row=0, column=2, sticky="ew", padx=(6, 0))

        # 一覧
        tk.Label(
            body, text="一覧", font=(FONT, 10, "bold"), bg=BG, fg=MUTED
        ).pack(anchor="w")

        list_wrap = tk.Frame(body, bg=CARD)
        list_wrap.pack(fill="both", expand=True, pady=(4, 0))

        scroll = tk.Scrollbar(list_wrap, bd=0, relief="flat",
                              troughcolor=CARD, bg=BUTTON)
        scroll.pack(side="right", fill="y")

        self.listbox = tk.Listbox(
            list_wrap, font=(FONT, 11), bg=CARD, fg=TEXT,
            selectbackground=INFO, selectforeground=SURFACE,
            relief="flat", bd=0, highlightthickness=0, activestyle="none",
            yscrollcommand=scroll.set
        )
        self.listbox.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        scroll.config(command=self.listbox.yview)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        self.listbox.bind("<Double-Button-1>", lambda e: self.apply_name())

        # ステータスバー
        self.status_var = tk.StringVar(value="準備中...")
        self.status_label = tk.Label(
            r, textvariable=self.status_var, font=(FONT, 10),
            bg=SURFACE, fg=MUTED, anchor="w"
        )
        self.status_label.pack(fill="x", side="bottom", ipady=7, ipadx=20)

        # キーボード
        r.bind("<Up>", lambda e: self.prev_name())
        r.bind("<Left>", lambda e: self.prev_name())
        r.bind("<Down>", lambda e: self.next_name())
        r.bind("<Right>", lambda e: self.next_name())
        r.bind("<Return>", lambda e: self.apply_name())
        r.bind("<Control-o>", lambda e: self.browse_csv())
        r.bind("<F5>", lambda e: self.reload_csv())

    def set_status(self, message, color=MUTED):
        self.status_var.set(message)
        self.status_label.configure(fg=color)

    # ------------------------------------------------------------ Shogun
    def _connect(self):
        if not HAS_SDK:
            self.conn_var.set("● SDK なし")
            self.conn_label.configure(fg=ERROR)
            return
        try:
            client = Client(HOST)
            self.capture = CaptureServices(client)
            self.conn_var.set("● 接続済み")
            self.conn_label.configure(fg=SUCCESS)
        except Exception as exc:  # 接続失敗でもUIは使えるようにする
            self.capture = None
            self.conn_var.set("● 未接続")
            self.conn_label.configure(fg=ERROR)
            print("Shogun Live への接続に失敗しました: {}".format(exc))

    # --------------------------------------------------------------- CSV
    def _load_initial_csv(self):
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, DEFAULT_CSV)
        if os.path.exists(path):
            self.load_csv(path)
        else:
            self.set_status("CSVを選んでください（参照... / Ctrl+O）", WARNING)

    def browse_csv(self):
        path = filedialog.askopenfilename(
            title="キャプチャ名のCSVを選択",
            filetypes=[("CSV", "*.csv"), ("すべてのファイル", "*.*")]
        )
        if path:
            self.load_csv(path)

    def reload_csv(self):
        if self.csv_path:
            self.load_csv(self.csv_path, keep_index=True)

    def load_csv(self, path, keep_index=False):
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = [row for row in csv.reader(f) if row and row[0].strip()]
        except (OSError, UnicodeDecodeError) as exc:
            self.set_status("CSVを読み込めません: {}".format(exc), ERROR)
            return

        self.csv_path = path
        self.names = [row[0].strip() for row in rows]
        self.invalid = [
            (i + 1, name) for i, name in enumerate(self.names)
            if not is_ascii_safe(name)
        ]
        self.file_var.set(os.path.basename(path))

        if not keep_index or self.index >= len(self.names):
            self.index = 0

        self._fill_listbox()
        self._refresh()

        if not self.names:
            self.set_status("CSVに名前がありません", ERROR)
        elif self.invalid:
            first = self.invalid[0]
            self.set_status(
                "{}件読み込み / 使用できない文字を含む名前が{}件（{}行目: {}）".format(
                    len(self.names), len(self.invalid), first[0], first[1]
                ),
                WARNING
            )
        else:
            self.set_status("{}件読み込みました".format(len(self.names)), SUCCESS)

    def _fill_listbox(self):
        self.listbox.delete(0, "end")
        for i, name in enumerate(self.names):
            ok = is_ascii_safe(name)
            mark = "  " if ok else "⚠ "
            self.listbox.insert("end", "{}{:>3}.  {}".format(mark, i + 1, name))
            if not ok:
                self.listbox.itemconfig(i, foreground=WARNING)

    # ------------------------------------------------------------ 表示更新
    def _refresh(self):
        total = len(self.names)
        has_items = total > 0
        state = "normal" if has_items else "disabled"
        for btn in (self.prev_btn, self.next_btn, self.set_btn):
            btn.configure(state=state)

        if not has_items:
            self.name_var.set("—")
            self.count_var.set("0 / 0")
            self.note_var.set("")
            return

        name = self.names[self.index]
        self.name_var.set(name)
        self.count_var.set("{} / {}".format(self.index + 1, total))

        if is_ascii_safe(name):
            self.name_label.configure(fg=TEXT)
            self.note_var.set("")
            self.set_btn.configure(state="normal")
        else:
            self.name_label.configure(fg=WARNING)
            self.note_var.set("全角文字・空白は使用できません")
            self.set_btn.configure(state="disabled")

        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.index)
        self.listbox.see(self.index)

    # -------------------------------------------------------------- 操作
    def on_select(self, _event):
        selection = self.listbox.curselection()
        if selection:
            self.index = selection[0]
            self._refresh()

    def next_name(self):
        if self.names:
            self.index = (self.index + 1) % len(self.names)
            self._refresh()

    def prev_name(self):
        if self.names:
            self.index = (self.index - 1) % len(self.names)
            self._refresh()

    def apply_name(self):
        if not self.names:
            return
        name = self.names[self.index]
        if not is_ascii_safe(name):
            self.set_status("「{}」は使用できない文字を含みます".format(name), ERROR)
            return
        if self.capture is None:
            self._connect()
        if self.capture is None:
            self.set_status("Shogun Live に接続できません", ERROR)
            return
        try:
            self.capture.set_capture_name(name)
        except Exception as exc:
            self.set_status("設定に失敗しました: {}".format(exc), ERROR)
            self.conn_var.set("● 未接続")
            self.conn_label.configure(fg=ERROR)
            self.capture = None
            return
        self.set_status("キャプチャ名を「{}」に設定しました".format(name), SUCCESS)


def main():
    root = tk.Tk()
    CaptureNameApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
