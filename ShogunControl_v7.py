from vicon_core_api import *
from shogun_live_api import CaptureServices, PlaybackServices, SubjectServices
import csv
import tkinter as tk
from tkinter import messagebox, filedialog
import os
import traceback

DARK_BG      = "#1E1E2E"
DARK_CARD    = "#313244"
DARK_ACCENT  = "#89DCEB"
DARK_TEXT    = "#CDD6F4"
DARK_BUTTON  = "#45475A"
DARK_SURFACE = "#181825"
DARK_SUCCESS = "#A6E3A1"
DARK_ERROR   = "#F38BA8"
DARK_INFO    = "#89B4FA"
DARK_MUTED   = "#6C7086"

LIGHT_BG      = "#F5F5FA"
LIGHT_CARD    = "#FFFFFF"
LIGHT_ACCENT  = "#7C3AED"
LIGHT_TEXT    = "#1A1B26"
LIGHT_BUTTON  = "#E2E8F0"
LIGHT_SUCCESS = "#40A02B"
LIGHT_ERROR   = "#D20F39"
LIGHT_INFO    = "#1E66F5"
LIGHT_MUTED   = "#8C8FA1"

FONT = "Yu Gothic UI"


def add_hover_effect(widget, enter_color, leave_color):
    widget.bind("<Enter>", lambda _: widget.configure(background=enter_color))
    widget.bind("<Leave>", lambda _: widget.configure(background=leave_color))


class SettingsDialog:
    def __init__(self, parent, is_dark_mode=False):
        self.result = None
        self.parent = parent
        self.is_dark_mode = is_dark_mode

        self.bg_color     = DARK_BG     if is_dark_mode else LIGHT_BG
        self.fg_color     = DARK_TEXT   if is_dark_mode else LIGHT_TEXT
        self.card_color   = DARK_CARD   if is_dark_mode else LIGHT_CARD
        self.accent_color = DARK_ACCENT if is_dark_mode else LIGHT_ACCENT
        self.button_color = DARK_BUTTON if is_dark_mode else LIGHT_BUTTON

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Shogun 接続設定")
        self.dialog.geometry("600x580")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.dialog.configure(bg=self.bg_color)
        self.dialog.grab_set()

        self.ip_address = tk.StringVar(value="localhost")
        self.csv_path   = tk.StringVar(value="Test.csv")
        self.load_previous_settings()

        self.create_widgets()

        self.dialog.update_idletasks()
        x = (self.parent.winfo_screenwidth()  - 600) // 2
        y = (self.parent.winfo_screenheight() - 580) // 2
        self.dialog.geometry(f"600x580+{x}+{y}")

        self.dialog.lift()
        self.dialog.focus_force()
        self.dialog.attributes('-topmost', True)
        self.dialog.after(500, lambda: self.dialog.attributes('-topmost', False))

    def load_previous_settings(self):
        try:
            if os.path.exists("shogun_settings.csv"):
                with open("shogun_settings.csv", "r") as f:
                    settings = next(csv.reader(f), None)
                    if settings and len(settings) >= 2:
                        self.ip_address.set(settings[0])
                        self.csv_path.set(settings[1])
        except Exception as e:
            print(f"設定の読み込みエラー: {e}")

    def save_settings(self):
        try:
            with open("shogun_settings.csv", "w", newline='') as f:
                csv.writer(f).writerow([self.ip_address.get(), self.csv_path.get()])
        except Exception as e:
            print(f"設定の保存エラー: {e}")

    def create_widgets(self):
        main_frame = tk.Frame(self.dialog, bg=self.bg_color, padx=25, pady=25)
        main_frame.pack(fill=tk.BOTH, expand=True)

        logo_frame = tk.Frame(main_frame, bg=self.bg_color, height=60)
        logo_frame.pack(fill=tk.X, pady=(0, 20))
        tk.Label(logo_frame, text="Shogun 接続設定",
                 font=(FONT, 18, "bold"),
                 bg=self.bg_color, fg=self.fg_color).pack(side=tk.LEFT)

        # IPアドレスカード
        ip_frame = tk.Frame(main_frame, bg=self.card_color, padx=20, pady=20)
        ip_frame.pack(fill=tk.X, pady=10)
        tk.Label(ip_frame, text="Shogun サーバーIPアドレス",
                 font=(FONT, 12, "bold"),
                 bg=self.card_color, fg=self.fg_color).pack(anchor=tk.W, pady=(0, 10))
        tk.Entry(ip_frame, textvariable=self.ip_address,
                 font=(FONT, 12), width=30,
                 bg=self.bg_color, fg=self.fg_color,
                 insertbackground=self.fg_color).pack(fill=tk.X, pady=5, ipady=8)
        tk.Label(ip_frame, text="例: 192.168.1.100 または localhost",
                 font=(FONT, 11),
                 bg=self.card_color, fg=self.accent_color).pack(anchor=tk.W, padx=5, pady=(5, 0))

        # CSVファイルカード
        csv_frame = tk.Frame(main_frame, bg=self.card_color, padx=20, pady=20)
        csv_frame.pack(fill=tk.X, pady=15)
        tk.Label(csv_frame, text="キャプチャ名リストCSVファイル",
                 font=(FONT, 12, "bold"),
                 bg=self.card_color, fg=self.fg_color).pack(anchor=tk.W, pady=(0, 10))

        csv_entry_frame = tk.Frame(csv_frame, bg=self.card_color)
        csv_entry_frame.pack(fill=tk.X, pady=5)
        tk.Entry(csv_entry_frame, textvariable=self.csv_path,
                 font=(FONT, 12),
                 bg=self.bg_color, fg=self.fg_color,
                 insertbackground=self.fg_color).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        tk.Button(csv_entry_frame, text="参照...",
                  font=(FONT, 10), command=self.browse_csv,
                  bg=self.button_color, fg=self.fg_color,
                  relief="flat", padx=10, pady=5,
                  activebackground=self.accent_color,
                  activeforeground=self.bg_color).pack(side=tk.LEFT, padx=(10, 0))

        tk.Label(csv_frame, text="CSVファイルの各行の最初の列がキャプチャ名として読み込まれます",
                 font=(FONT, 11),
                 bg=self.card_color, fg=self.accent_color).pack(anchor=tk.W, padx=5, pady=(5, 0))

        # ボタン
        button_frame = tk.Frame(main_frame, bg=self.bg_color)
        button_frame.pack(fill=tk.X, pady=20)

        cancel_btn = tk.Button(button_frame, text="キャンセル",
                               font=(FONT, 11), command=self.on_cancel,
                               bg=self.bg_color, fg=self.fg_color,
                               relief="flat", padx=15, pady=8,
                               bd=1, highlightthickness=1,
                               highlightbackground=self.fg_color,
                               activebackground=self.bg_color)
        cancel_btn.pack(side=tk.RIGHT, padx=5)
        add_hover_effect(cancel_btn,
                         self.button_color if self.is_dark_mode else "#E8E8E8",
                         self.bg_color)

        ok_btn = tk.Button(button_frame, text="接続",
                           font=(FONT, 11, "bold"), command=self.on_ok,
                           bg=self.accent_color, fg=self.bg_color,
                           relief="flat", padx=20, pady=8,
                           activebackground=self.accent_color,
                           activeforeground=self.bg_color)
        ok_btn.pack(side=tk.RIGHT, padx=5)
        add_hover_effect(ok_btn,
                         "#7AC1CF" if self.is_dark_mode else "#6B2AE3",
                         self.accent_color)

        self.dialog.bind("<Return>", lambda _: self.on_ok())

    def browse_csv(self):
        filepath = filedialog.askopenfilename(
            title="キャプチャ名リストCSVファイルを選択",
            filetypes=[("CSVファイル", "*.csv"), ("すべてのファイル", "*.*")]
        )
        if filepath:
            self.csv_path.set(filepath)

    def on_ok(self):
        ip       = self.ip_address.get().strip()
        csv_path = self.csv_path.get().strip()
        if not ip:
            messagebox.showerror("エラー", "IPアドレスを入力してください")
            return
        if not csv_path:
            messagebox.showerror("エラー", "CSVファイルを選択してください")
            return
        if not os.path.exists(csv_path):
            messagebox.showerror("エラー", f"CSVファイルが見つかりません: {csv_path}")
            return
        self.save_settings()
        self.result = {"ip_address": ip, "csv_path": csv_path}
        self.dialog.destroy()

    def on_cancel(self):
        self.dialog.destroy()


class ShogunButtonUI:
    def __init__(self, root, ip_address="localhost", csv_file="Test.csv"):
        self.root = root
        self.root.title("Shogun Control Interface")
        self.root.geometry("1920x1080")
        self.is_dark_mode = True
        self._apply_theme()
        self.root.configure(bg=self.bg_color)

        self.count            = 0
        self.capture_name_var = tk.StringVar(value="初期化中...")
        self.status_var       = tk.StringVar(value="準備中...")
        self.count_var        = tk.StringVar(value="0 / 0")
        self.capture_names    = []
        self.progress_canvas       = None
        self.progress_animation_id = None

        self.connect_to_shogun(ip_address)
        self.file = csv_file
        self.load_capture_names()

        if self.capture_names:
            try:
                self.ShogunCap.set_capture_name(self.capture_names[0])
            except Exception as e:
                print(f"初期キャプチャ名設定エラー: {e}")

        self.create_ui()
        self.create_progress_canvas()

    def _apply_theme(self):
        dm = self.is_dark_mode
        self.bg_color     = DARK_BG      if dm else LIGHT_BG
        self.card_color   = DARK_CARD    if dm else LIGHT_CARD
        self.accent_color = DARK_ACCENT  if dm else LIGHT_ACCENT
        self.text_color   = DARK_TEXT    if dm else LIGHT_TEXT
        self.button_color = DARK_BUTTON  if dm else LIGHT_BUTTON

    def connect_to_shogun(self, ip_address):
        try:
            self.c          = Client(ip_address)
            self.ShogunCap  = CaptureServices(self.c)
            self.ShogunPlay = PlaybackServices(self.c)
            self.ShogunSub  = SubjectServices(self.c)
            print(f"Shogunサーバー {ip_address} に接続しました")
        except Exception as e:
            print(f"Shogun接続エラー: {e}")
            messagebox.showerror("接続エラー", f"Shogunサーバーへの接続に失敗しました: {e}")
            raise

    def load_capture_names(self):
        try:
            with open(self.file) as f:
                self.capture_names = [row[0] for row in csv.reader(f) if row]
            if not self.capture_names:
                messagebox.showwarning("警告", f"{self.file}にキャプチャ名が見つかりません")
                self.capture_names = ["デフォルトキャプチャ"]
        except Exception as e:
            messagebox.showerror("エラー", f"CSVファイル読み込みエラー: {e}")
            self.capture_names = ["デフォルトキャプチャ"]

    def _run_op(self, processing_msg, func):
        """Shogun操作の共通パターン: 進捗表示・エラー処理を統一"""
        self.update_status(processing_msg, "processing")
        self.show_progress_animation(True)
        try:
            result = func()
            self.root.after(500, lambda: self.show_progress_animation(False))
            return result
        except Exception as e:
            self.show_progress_animation(False)
            self.update_status(f"エラー: {e}", "error")
            messagebox.showerror("エラー", str(e))
            return None

    def create_ui(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        self.main_container = tk.Frame(self.root, bg=self.bg_color)
        self.main_container.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)
        self.main_container.columnconfigure(0, weight=1)
        self.main_container.rowconfigure(0, weight=0)
        self.main_container.rowconfigure(1, weight=1)
        self.main_container.rowconfigure(2, weight=0)

        self.create_header()

        self.content_frame = tk.Frame(self.main_container, bg=self.bg_color)
        self.content_frame.grid(row=1, column=0, sticky="nsew", pady=20)
        self.content_frame.columnconfigure(0, weight=1)
        self.content_frame.rowconfigure(0, weight=1)
        self.content_frame.rowconfigure(1, weight=1)
        self.content_frame.rowconfigure(2, weight=1)

        self.create_capture_control_panel()
        self.create_capture_name_panel()
        self.create_subject_control_panel()
        self.create_status_bar()

        self.update_capture_name_display()
        self.update_status("準備完了", "success")

    def create_progress_canvas(self):
        self.progress_canvas = tk.Canvas(self.root, width=100, height=100,
                                         bg=self.bg_color, highlightthickness=0)
        self.progress_canvas.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        self.progress_canvas.place_forget()

    def show_progress_animation(self, show=True):
        if not hasattr(self, "progress_canvas") or self.progress_canvas is None:
            return
        if show:
            self.progress_canvas.config(bg=self.bg_color)
            self.progress_canvas.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
            self.animate_progress(0)
        else:
            if self.progress_animation_id:
                self.progress_canvas.after_cancel(self.progress_animation_id)
                self.progress_animation_id = None
            self.progress_canvas.place_forget()

    def animate_progress(self, angle):
        if not hasattr(self, "progress_canvas") or self.progress_canvas is None:
            return
        self.progress_canvas.delete("progress")
        self.progress_canvas.create_arc(20, 20, 80, 80,
                                        start=angle, extent=60, tags="progress",
                                        outline=self.accent_color, width=5, style="arc")
        angle = (angle + 10) % 360
        self.progress_animation_id = self.progress_canvas.after(
            40, lambda: self.animate_progress(angle))

    def create_header(self):
        outer_header = tk.Frame(self.main_container, bg=self.bg_color)
        outer_header.grid(row=0, column=0, sticky="ew")

        header_frame = tk.Frame(outer_header, bg=self.bg_color, height=70)
        header_frame.pack(fill=tk.X)

        title_block = tk.Frame(header_frame, bg=self.bg_color)
        title_block.pack(side=tk.LEFT)
        tk.Label(title_block, text="Shogun Control",
                 font=(FONT, 22, "bold"),
                 bg=self.bg_color, fg=self.text_color).pack(anchor=tk.W)
        tk.Label(title_block, text="Motion Capture Interface",
                 font=(FONT, 10),
                 bg=self.bg_color, fg=DARK_MUTED if self.is_dark_mode else LIGHT_MUTED).pack(anchor=tk.W)

        tk.Frame(header_frame, bg=self.accent_color, width=2).pack(
            side=tk.LEFT, fill=tk.Y, padx=20, pady=8)

        name_block = tk.Frame(header_frame, bg=self.bg_color)
        name_block.pack(side=tk.LEFT)
        tk.Label(name_block, text="CURRENT CAPTURE",
                 font=(FONT, 9, "bold"),
                 bg=self.bg_color,
                 fg=DARK_MUTED if self.is_dark_mode else LIGHT_MUTED).pack(anchor=tk.W)
        tk.Label(name_block, textvariable=self.capture_name_var,
                 font=(FONT, 18, "bold"),
                 bg=self.bg_color, fg=self.accent_color).pack(anchor=tk.W)

        controls_frame = tk.Frame(header_frame, bg=self.bg_color)
        controls_frame.pack(side=tk.RIGHT, padx=(0, 5))
        ctrl_btn_style = dict(
            font=(FONT, 15), bg=self.bg_color, fg=self.text_color,
            bd=0, relief="flat", padx=10, pady=6,
            activebackground=self.button_color, cursor="hand2"
        )
        tk.Button(controls_frame, text="?",
                  command=self.show_help, **ctrl_btn_style).pack(side=tk.RIGHT, padx=3)
        tk.Button(controls_frame, text="◐" if self.is_dark_mode else "◑",
                  command=self.toggle_theme, **ctrl_btn_style).pack(side=tk.RIGHT, padx=3)
        tk.Button(controls_frame, text="⚙",
                  command=self.open_settings, **ctrl_btn_style).pack(side=tk.RIGHT, padx=3)

        tk.Frame(outer_header, bg=self.accent_color, height=2).pack(fill=tk.X)

    def create_card(self, parent, title, height=None):
        wrapper = tk.Frame(parent, bg=self.accent_color, bd=0)
        if height:
            wrapper.configure(height=height)
        card = tk.Frame(wrapper, bg=self.card_color, padx=22, pady=18, relief="flat", bd=0)
        card.pack(fill=tk.BOTH, expand=True, padx=(4, 0))
        tk.Label(card, text=title, font=(FONT, 14, "bold"),
                 bg=self.card_color, fg=self.text_color).pack(anchor=tk.W, pady=(0, 6))
        tk.Frame(card, bg=self.accent_color, height=1).pack(fill=tk.X, pady=(0, 12))
        content = tk.Frame(card, bg=self.card_color)
        content.pack(fill=tk.BOTH, expand=True)
        return wrapper, content

    def create_capture_control_panel(self):
        capture_frame = tk.Frame(self.content_frame, bg=self.bg_color)
        capture_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 20))
        card, content = self.create_card(capture_frame, "キャプチャ操作")
        card.pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(content, bg=self.card_color)
        btn_frame.pack(fill=tk.BOTH, expand=True)
        for i in range(4):
            btn_frame.columnconfigure(i, weight=1)

        success = DARK_SUCCESS if self.is_dark_mode else LIGHT_SUCCESS
        error   = DARK_ERROR   if self.is_dark_mode else LIGHT_ERROR
        info    = DARK_INFO    if self.is_dark_mode else LIGHT_INFO

        btn_configs = [
            ("▶  キャプチャ開始", self.start_capture,  success,           "#1E1E2E", "#8BD5A5"),
            ("■  キャプチャ停止", self.stop_capture,   error,             "#1E1E2E", "#D67F95"),
            ("⏵  レビュー開始",   self.enter_playback, info,              "#1E1E2E", "#6E96D6"),
            ("✕  レビュー終了",   self.exit_review,    self.button_color, self.text_color, self.accent_color),
        ]
        for col, (label, cmd, bg, fg, hover_bg) in enumerate(btn_configs):
            btn = tk.Button(btn_frame, text=label,
                            font=(FONT, 13, "bold"),
                            bg=bg, fg=fg,
                            activebackground=hover_bg, activeforeground=fg,
                            command=cmd, relief="flat", bd=0,
                            pady=18, padx=8, cursor="hand2")
            btn.grid(row=0, column=col, padx=8, pady=6, sticky="nsew")
            add_hover_effect(btn, hover_bg, bg)

    def create_capture_name_panel(self):
        name_frame = tk.Frame(self.content_frame, bg=self.bg_color)
        name_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 20))
        card, content = self.create_card(name_frame, "キャプチャ名選択")
        card.pack(fill=tk.BOTH, expand=True)

        nav = tk.Frame(content, bg=self.card_color)
        nav.pack(fill=tk.BOTH, expand=True)
        nav.columnconfigure(0, weight=1)
        nav.columnconfigure(1, weight=3)
        nav.columnconfigure(2, weight=1)

        nav_btn_style = dict(
            font=(FONT, 20, "bold"),
            bg=self.button_color, fg=self.accent_color,
            relief="flat", bd=0, padx=16, pady=12, cursor="hand2",
            activebackground=self.accent_color, activeforeground=self.bg_color
        )

        back_btn = tk.Button(nav, text="❮", command=self.back_capture_name, **nav_btn_style)
        back_btn.grid(row=0, column=0, padx=(0, 10), pady=8, sticky="nsew")
        add_hover_effect(back_btn, self.accent_color, self.button_color)

        self.update_count_display()
        count_frame = tk.Frame(nav, bg=self.card_color)
        count_frame.grid(row=0, column=1, padx=10, pady=8, sticky="nsew")
        tk.Label(count_frame, textvariable=self.count_var,
                 font=(FONT, 20, "bold"),
                 bg=self.card_color, fg=self.text_color).pack(expand=True)
        tk.Label(count_frame, text="テイク",
                 font=(FONT, 10),
                 bg=self.card_color,
                 fg=DARK_MUTED if self.is_dark_mode else LIGHT_MUTED).pack()

        next_btn = tk.Button(nav, text="❯", command=self.next_capture_name, **nav_btn_style)
        next_btn.grid(row=0, column=2, padx=(10, 0), pady=8, sticky="nsew")
        add_hover_effect(next_btn, self.accent_color, self.button_color)

    def update_count_display(self):
        if self.capture_names:
            self.count_var.set(f"{self.count + 1} / {len(self.capture_names)}")
        else:
            self.count_var.set("0 / 0")

    def create_subject_control_panel(self):
        subject_frame = tk.Frame(self.content_frame, bg=self.bg_color)
        subject_frame.grid(row=2, column=0, sticky="nsew")
        card, content = self.create_card(subject_frame, "サブジェクト操作")
        card.pack(fill=tk.BOTH, expand=True)

        reboot_color = DARK_ERROR if self.is_dark_mode else LIGHT_ERROR
        reboot_btn = tk.Button(content, text="⟳  全サブジェクト再起動",
                               font=(FONT, 14, "bold"),
                               bg=reboot_color, fg="#1E1E2E",
                               command=self.all_subject_reboot,
                               relief="flat", bd=0, padx=20, pady=16, cursor="hand2",
                               activebackground=self._dim_color(reboot_color),
                               activeforeground="#1E1E2E")
        reboot_btn.pack(expand=True, padx=40, pady=15, fill=tk.X)
        add_hover_effect(reboot_btn, self._dim_color(reboot_color), reboot_color)

    def create_status_bar(self):
        tk.Frame(self.main_container, bg=self.button_color, height=1).grid(
            row=2, column=0, sticky="ew")

        surface = DARK_SURFACE if self.is_dark_mode else "#EBEBF5"
        status_frame = tk.Frame(self.main_container, bg=surface, height=40)
        status_frame.grid(row=3, column=0, sticky="ew")
        self.main_container.rowconfigure(3, weight=0)

        self._status_dot_color = "#4CAF50"
        self._status_dot_bright = True
        self.status_dot = tk.Label(status_frame, text="⬤", font=(FONT, 10),
                                   bg=surface, fg=self._status_dot_color)
        self.status_dot.pack(side=tk.LEFT, padx=(15, 6))

        tk.Label(status_frame, textvariable=self.status_var,
                 font=(FONT, 11), bg=surface, fg=self.text_color).pack(side=tk.LEFT)

        version_frame = tk.Frame(status_frame, bg=self.accent_color, padx=8, pady=2)
        version_frame.pack(side=tk.RIGHT, padx=15, pady=6)
        tk.Label(version_frame, text="v7.0", font=(FONT, 9, "bold"),
                 bg=self.accent_color, fg=DARK_BG).pack()

        self._pulse_status()

    def _pulse_status(self):
        if not hasattr(self, "status_dot") or not self.status_dot.winfo_exists():
            return
        try:
            self._status_dot_bright = not self._status_dot_bright
            color = self._status_dot_color if self._status_dot_bright else self._dim_color(self._status_dot_color)
            self.status_dot.configure(fg=color)
            self.root.after(700, self._pulse_status)
        except Exception:
            pass

    def _dim_color(self, hex_color):
        try:
            r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
            return f"#{int(r*0.45):02x}{int(g*0.45):02x}{int(b*0.45):02x}"
        except Exception:
            return hex_color

    def update_capture_name_display(self):
        if 0 <= self.count < len(self.capture_names):
            self.capture_name_var.set(self.capture_names[self.count])
        else:
            self.capture_name_var.set("無効なインデックス")
        self.update_count_display()

    def start_capture(self):
        def op():
            self.ShogunCap.start_capture()
            self.update_status("キャプチャを開始しました", "success")
        self._run_op("キャプチャを開始しています...", op)

    def stop_capture(self):
        def op():
            self.ShogunCap.stop_capture(0)
            self.update_status("キャプチャを停止しました", "success")
        self._run_op("キャプチャを停止しています...", op)

    def enter_playback(self):
        def op():
            capture_list = self.ShogunCap.latest_capture_name()
            if not capture_list or len(capture_list) < 3:
                raise ValueError("キャプチャリストが正しくありません")
            self.ShogunPlay.enter_capture_review(capture_list[2])
            self.ShogunPlay.play()
            self.update_status(f"レビュー中: {capture_list[2]}", "success")
        self._run_op("レビューを開始しています...", op)

    def exit_review(self):
        def op():
            self.ShogunPlay.exit_review()
            self.update_status("レビューを終了しました", "success")
        self._run_op("レビューを終了しています...", op)

    def next_capture_name(self):
        if self.count < len(self.capture_names) - 1:
            self.count += 1
            self.update_capture_name()
        else:
            self.update_status("リストの最後に到達しました", "info")
            messagebox.showinfo("情報", "リストの最後に到達しました")

    def back_capture_name(self):
        if self.count > 0:
            self.count -= 1
            self.update_capture_name()
        else:
            self.update_status("リストの先頭に到達しました", "info")
            messagebox.showinfo("情報", "リストの先頭に到達しました")

    def update_capture_name(self):
        try:
            if 0 <= self.count < len(self.capture_names):
                self.ShogunCap.set_capture_name(self.capture_names[self.count])
                self.update_capture_name_display()
                self.update_status(f"キャプチャ名を変更: {self.capture_names[self.count]}", "info")
        except Exception as e:
            self.update_status(f"エラー: {e}", "error")
            messagebox.showerror("エラー", f"キャプチャ名更新エラー: {e}")

    def all_subject_reboot(self):
        def op():
            self.ShogunSub.set_all_subjects_enabled(False)
            self.root.after(500)
            self.ShogunSub.set_all_subjects_enabled(True)
            self.update_status("全サブジェクトを再起動しました", "success")
        self._run_op("サブジェクト再起動中...", op)

    def update_status(self, message, status_type="info"):
        self.status_var.set(message)
        color_map = {
            "success":    "#4CAF50",
            "error":      "#F44336",
            "processing": "#FFC107",
            "info":       "#2196F3",
        }
        self._status_dot_color = color_map.get(status_type, "#2196F3")
        if hasattr(self, "status_dot") and self.status_dot.winfo_exists():
            self.status_dot.configure(fg=self._status_dot_color)
        print(f"ステータス: {message}")
        self.root.update()

    def toggle_theme(self):
        self.is_dark_mode = not self.is_dark_mode
        self._apply_theme()
        self.create_ui()

    def open_settings(self):
        try:
            settings_dialog = SettingsDialog(self.root, self.is_dark_mode)
            self.root.wait_window(settings_dialog.dialog)
            if not settings_dialog.result:
                return

            self.show_progress_animation(True)
            try:
                self.c.disconnect()
            except Exception:
                pass

            new_ip  = settings_dialog.result["ip_address"]
            new_csv = settings_dialog.result["csv_path"]
            self.connect_to_shogun(new_ip)
            self.file = new_csv
            self.load_capture_names()
            self.show_progress_animation(False)

            if self.capture_names:
                self.count = 0
                self.ShogunCap.set_capture_name(self.capture_names[0])
                self.update_capture_name_display()

            self.update_status(
                f"新しい設定を適用: サーバー {new_ip}, CSV {os.path.basename(new_csv)}", "success")
            messagebox.showinfo("設定更新", "新しい接続設定が適用されました")
        except Exception as e:
            self.show_progress_animation(False)
            self.update_status(f"設定エラー: {e}", "error")
            messagebox.showerror("設定エラー", f"新しい設定の適用に失敗しました: {e}")

    def show_help(self):
        help_dialog = tk.Toplevel(self.root)
        help_dialog.title("使い方")
        help_dialog.geometry("650x500")
        help_dialog.configure(bg=self.bg_color)
        help_dialog.transient(self.root)
        help_dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width()  - 650) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 500) // 2
        help_dialog.geometry(f"+{x}+{y}")

        content = tk.Frame(help_dialog, bg=self.bg_color, padx=30, pady=30)
        content.pack(fill=tk.BOTH, expand=True)
        tk.Label(content, text="Shogun Control インターフェースの使い方",
                 font=(FONT, 18, "bold"),
                 bg=self.bg_color, fg=self.text_color).pack(anchor="w", pady=(0, 20))

        sections = [
            ("キャプチャ操作", [
                "キャプチャ開始: モーションキャプチャを開始します",
                "キャプチャ停止: 現在のキャプチャを停止します",
                "レビュー開始: 最後のキャプチャのレビューを開始します",
                "レビュー終了: レビューモードを終了します",
            ]),
            ("キャプチャ名選択", [
                "前/次のキャプチャ名: CSVファイルから読み込まれたキャプチャ名リストを移動します",
                "現在のキャプチャ名はアプリの上部に常に表示されます",
            ]),
            ("サブジェクト操作", [
                "全サブジェクト再起動: すべてのサブジェクトを無効化し、再度有効化します",
                "この操作は注意して使用してください",
            ]),
            ("その他の機能", [
                "設定: 接続先サーバーとCSVファイルを変更できます",
                "テーマ切替: ダークモードとライトモードを切り替えられます",
                "状態表示: 画面下部のステータスバーで現在の状態を確認できます",
            ]),
        ]
        for section_title, items in sections:
            section_frame = tk.Frame(content, bg=self.card_color, padx=20, pady=15)
            section_frame.pack(fill=tk.X, pady=10)
            tk.Label(section_frame, text=section_title,
                     font=(FONT, 14, "bold"),
                     bg=self.card_color, fg=self.accent_color).pack(anchor="w", pady=(0, 10))
            for item in items:
                row = tk.Frame(section_frame, bg=self.card_color)
                row.pack(fill=tk.X, pady=3)
                tk.Label(row, text="•", font=(FONT, 12, "bold"),
                         bg=self.card_color, fg=self.accent_color).pack(side=tk.LEFT, padx=(0, 8))
                tk.Label(row, text=item, font=(FONT, 12),
                         bg=self.card_color, fg=self.text_color,
                         justify=tk.LEFT, wraplength=550).pack(side=tk.LEFT, fill=tk.X)

        tk.Button(content, text="閉じる", font=(FONT, 12, "bold"),
                  bg=self.accent_color, fg=self.bg_color,
                  command=help_dialog.destroy,
                  relief="raised", borderwidth=2, padx=20, pady=8).pack(pady=20)
        help_dialog.focus_set()


def main():
    try:
        root = tk.Tk()
        root.title("Shogun Control")

        try:
            if os.path.exists("shogun_icon.ico"):
                root.iconbitmap("shogun_icon.ico")
        except Exception:
            pass

        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()

        # スプラッシュ画面
        splash = tk.Toplevel(root)
        splash.overrideredirect(True)
        splash.geometry(f"400x200+{(screen_w-400)//2}+{(screen_h-200)//2}")
        splash.configure(bg=DARK_BG)
        splash.attributes('-topmost', True)
        tk.Label(splash, text="Shogun Control",
                 font=(FONT, 24, "bold"), bg=DARK_BG, fg=DARK_TEXT).pack(pady=(50, 10))
        tk.Label(splash, text="起動中...",
                 font=(FONT, 12), bg=DARK_BG, fg=DARK_ACCENT).pack()
        splash.update()

        def start_app():
            splash.destroy()
            if screen_w >= 1920 and screen_h >= 1080:
                root.geometry("1600x900+0+0")
            else:
                w, h = int(screen_w * 0.8), int(screen_h * 0.8)
                root.geometry(f"{w}x{h}+{(screen_w-w)//2}+{(screen_h-h)//2}")
            root.deiconify()

            settings_dialog = SettingsDialog(root, is_dark_mode=True)
            root.wait_window(settings_dialog.dialog)
            if not settings_dialog.result:
                root.destroy()
                return

            ip_address = settings_dialog.result["ip_address"]
            csv_path   = settings_dialog.result["csv_path"]
            try:
                ShogunButtonUI(root, ip_address, csv_path)
            except Exception as e:
                messagebox.showerror("エラー", f"アプリケーション起動エラー: {e}\n\n{traceback.format_exc()}")
                root.destroy()

        root.withdraw()
        root.after(1500, start_app)
        root.mainloop()

    except Exception as e:
        print(f"初期化エラー: {e}\n{traceback.format_exc()}")
        try:
            messagebox.showerror("致命的なエラー", f"アプリケーションの起動に失敗しました。\n{e}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
