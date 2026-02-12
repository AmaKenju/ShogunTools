from vicon_core_api import *
from shogun_live_api import CaptureServices, PlaybackServices, SubjectServices
import csv
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import time
import sys
import traceback
import threading
import queue
import json

# Vosk音声認識（オプション）
USE_VOSK = False
try:
    import vosk
    import sounddevice as sd
    USE_VOSK = True
except ImportError:
    print("vosk/sounddeviceがインストールされていません。音声認識は無効です。")

# Pillowがインストールされていない場合の代替方法
USE_PIL = False
try:
    from PIL import Image, ImageTk
    USE_PIL = True
except ImportError:
    print("PIL/Pillowがインストールされていません。一部の視覚効果が制限されます。")

# Global colors for theming
DARK_BG = "#1E1E2E"
DARK_CARD = "#313244"
DARK_ACCENT = "#89DCEB"
DARK_TEXT = "#CDD6F4"
DARK_BUTTON = "#45475A"
DARK_SURFACE = "#181825"
DARK_SUCCESS = "#A6E3A1"
DARK_ERROR = "#F38BA8"
DARK_INFO = "#89B4FA"
DARK_WARNING = "#FAB387"
DARK_MUTED = "#6C7086"

LIGHT_BG = "#F5F5FA"
LIGHT_CARD = "#FFFFFF"
LIGHT_ACCENT = "#7C3AED"
LIGHT_TEXT = "#1A1B26"
LIGHT_BUTTON = "#E2E8F0"
LIGHT_SUCCESS = "#40A02B"
LIGHT_ERROR = "#D20F39"
LIGHT_INFO = "#1E66F5"
LIGHT_WARNING = "#FE640B"
LIGHT_MUTED = "#8C8FA1"

FONT = "Yu Gothic UI"

# 音声コマンドマッピング（認識テキストに含まれるキーワード → アクション名）
VOICE_COMMANDS = {
    "start_capture": ["レック", "開始", "キャプチャ開始"],
    "stop_capture": ["カット", "停止", "キャプチャ停止"],
    "enter_playback": ["レビュー開始", "レビュー", "再生"],
    "exit_review": ["レビュー終了", "レビュー停止"],
    "next_capture": ["次へ", "次", "ネクスト"],
    "back_capture": ["前へ", "前", "バック"],
    "reboot_subjects": ["リブート", "再起動"],
}

# 表示用の日本語ラベル
VOICE_COMMAND_LABELS = {
    "start_capture": "キャプチャ開始",
    "stop_capture": "キャプチャ停止",
    "enter_playback": "レビュー開始",
    "exit_review": "レビュー終了",
    "next_capture": "次のキャプチャ名",
    "back_capture": "前のキャプチャ名",
    "reboot_subjects": "サブジェクト再起動",
}

class ModernWidget:
    """Helper class for modern widget styling"""

    @staticmethod
    def create_hover_effect(widget, enter_color, leave_color):
        """Add hover effect to any widget"""
        try:
            widget.bind("<Enter>", lambda e: widget.configure(background=enter_color))
            widget.bind("<Leave>", lambda e: widget.configure(background=leave_color))
        except Exception as e:
            print(f"ホバー効果の作成エラー: {str(e)}")

    @staticmethod
    def rounded_rect(canvas, x1, y1, x2, y2, radius=25, **kwargs):
        """Draw a rounded rectangle on a canvas"""
        try:
            points = [
                x1+radius, y1,
                x2-radius, y1,
                x2, y1,
                x2, y1+radius,
                x2, y2-radius,
                x2, y2,
                x2-radius, y2,
                x1+radius, y2,
                x1, y2,
                x1, y2-radius,
                x1, y1+radius,
                x1, y1
            ]
            return canvas.create_polygon(points, **kwargs, smooth=True)
        except Exception as e:
            print(f"角丸長方形の作成エラー: {str(e)}")
            # フォールバック: 通常の長方形
            return canvas.create_rectangle(x1, y1, x2, y2, **kwargs)

class VoiceController:
    """Voskを使用したオフライン音声認識コントローラー"""

    def __init__(self, model_path=None, command_queue=None):
        self.command_queue = command_queue or queue.Queue()
        self.is_running = False
        self._stream = None
        self._recognizer = None
        self._thread = None
        self._audio_queue = queue.Queue()

        if not USE_VOSK:
            print("Voskが利用できないため、音声認識は無効です。")
            return

        # モデルパスの自動検出
        if model_path is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            candidates = [
                os.path.join(script_dir, "vosk-model-small-ja"),
                os.path.join(script_dir, "vosk-model-ja"),
                os.path.join(script_dir, "model"),
            ]
            for path in candidates:
                if os.path.isdir(path):
                    model_path = path
                    break

        if model_path is None or not os.path.isdir(model_path):
            print(f"Voskモデルが見つかりません。音声認識は無効です。")
            print(f"日本語モデルをダウンロードして以下のいずれかに配置してください:")
            print(f"  - vosk-model-small-ja/")
            print(f"  - vosk-model-ja/")
            print(f"  - model/")
            self._model = None
            return

        try:
            vosk.SetLogLevel(-1)  # Voskのログ出力を抑制
            self._model = vosk.Model(model_path)
            print(f"Voskモデルを読み込みました: {model_path}")
        except Exception as e:
            print(f"Voskモデル読み込みエラー: {str(e)}")
            self._model = None

    def is_available(self):
        """音声認識が利用可能かどうか"""
        return USE_VOSK and self._model is not None

    def start(self):
        """音声認識を開始"""
        if not self.is_available():
            return False

        if self.is_running:
            return True

        try:
            sample_rate = 16000
            self._recognizer = vosk.KaldiRecognizer(self._model, sample_rate)
            self.is_running = True

            # sounddeviceのコールバックで音声データをキューに入れる
            def audio_callback(indata, frames, time_info, status):
                if status:
                    print(f"Audio status: {status}")
                if self.is_running:
                    self._audio_queue.put(bytes(indata))

            self._stream = sd.RawInputStream(
                samplerate=sample_rate,
                blocksize=8000,
                dtype="int16",
                channels=1,
                callback=audio_callback,
            )
            self._stream.start()

            # 認識処理用のバックグラウンドスレッド
            self._thread = threading.Thread(target=self._recognize_loop, daemon=True)
            self._thread.start()

            print("音声認識を開始しました")
            return True
        except Exception as e:
            print(f"音声認識開始エラー: {str(e)}")
            self.is_running = False
            return False

    def stop(self):
        """音声認識を停止"""
        self.is_running = False
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
        except Exception as e:
            print(f"音声ストリーム停止エラー: {str(e)}")

        # キューをクリア
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        print("音声認識を停止しました")

    def _recognize_loop(self):
        """バックグラウンドで音声認識を実行するループ"""
        while self.is_running:
            try:
                data = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if self._recognizer.AcceptWaveform(data):
                result = json.loads(self._recognizer.Result())
                text = result.get("text", "").strip()
                if text:
                    self._match_command(text)
            else:
                # 部分認識結果（リアルタイムフィードバック用）
                partial = json.loads(self._recognizer.PartialResult())
                partial_text = partial.get("partial", "").strip()
                if partial_text:
                    self.command_queue.put(("partial", partial_text))

    def _match_command(self, text):
        """認識テキストからコマンドをマッチング"""
        print(f"音声認識: 「{text}」")

        # 長いキーワードから先にマッチさせる（部分一致の誤爆を防ぐ）
        for action, keywords in sorted(
            VOICE_COMMANDS.items(),
            key=lambda x: max(len(k) for k in x[1]),
            reverse=True,
        ):
            for keyword in sorted(keywords, key=len, reverse=True):
                if keyword in text:
                    label = VOICE_COMMAND_LABELS.get(action, action)
                    print(f"音声コマンド検出: {label}")
                    self.command_queue.put(("command", action))
                    return

        # マッチしなかった場合
        self.command_queue.put(("unrecognized", text))


class SettingsDialog:
    def __init__(self, parent, is_dark_mode=False):
        self.result = None
        self.parent = parent
        self.is_dark_mode = is_dark_mode

        # Set theme colors
        self.bg_color = DARK_BG if is_dark_mode else LIGHT_BG
        self.fg_color = DARK_TEXT if is_dark_mode else LIGHT_TEXT
        self.card_color = DARK_CARD if is_dark_mode else LIGHT_CARD
        self.accent_color = DARK_ACCENT if is_dark_mode else LIGHT_ACCENT
        self.button_color = DARK_BUTTON if is_dark_mode else LIGHT_BUTTON

        # ダイアログ作成
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Shogun 接続設定")
        self.dialog.geometry("600x580")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.protocol("WM_DELETE_WINDOW", self.on_cancel)  # 閉じるボタンの処理

        # Configure theme
        self.dialog.configure(bg=self.bg_color)

        # モーダルダイアログにする
        self.dialog.grab_set()

        # 設定の初期値（前回の設定があれば読み込む）
        self.ip_address = tk.StringVar(value="localhost")
        self.csv_path = tk.StringVar(value="Test.csv")
        self.load_previous_settings()

        # UI作成
        self.create_widgets()

        # ウィンドウが表示されるのを確実にする
        self.dialog.update_idletasks()

        # 画面中央に配置
        try:
            screen_width = self.parent.winfo_screenwidth()
            screen_height = self.parent.winfo_screenheight()
            x = (screen_width - 600) // 2
            y = (screen_height - 580) // 2
            self.dialog.geometry(f"600x580+{x}+{y}")
        except Exception as e:
            print(f"ダイアログ位置の設定エラー: {str(e)}")

        # 必ず最前面に表示
        try:
            self.dialog.lift()
            self.dialog.focus_force()
            self.dialog.attributes('-topmost', True)
            self.dialog.update()
            # 少し待機してから最前面設定を解除
            self.dialog.after(500, lambda: self.dialog.attributes('-topmost', False))
        except Exception as e:
            print(f"ダイアログ表示の設定エラー: {str(e)}")

    def load_previous_settings(self):
        """前回の設定を読み込む（存在する場合）"""
        try:
            if os.path.exists("shogun_settings.csv"):
                with open("shogun_settings.csv", "r") as f:
                    reader = csv.reader(f)
                    settings = next(reader, None)
                    if settings and len(settings) >= 2:
                        self.ip_address.set(settings[0])
                        self.csv_path.set(settings[1])
        except Exception as e:
            print(f"設定の読み込みエラー: {str(e)}")

    def save_settings(self):
        """設定を保存"""
        try:
            with open("shogun_settings.csv", "w", newline='') as f:
                writer = csv.writer(f)
                writer.writerow([self.ip_address.get(), self.csv_path.get()])
        except Exception as e:
            print(f"設定の保存エラー: {str(e)}")

    def create_widgets(self):
        try:
            main_frame = tk.Frame(self.dialog, bg=self.bg_color, padx=25, pady=25)
            main_frame.pack(fill=tk.BOTH, expand=True)

            # Logo or Icon (placeholder)
            logo_frame = tk.Frame(main_frame, bg=self.bg_color, height=60)
            logo_frame.pack(fill=tk.X, pady=(0, 20))

            # Try to load logo if exists and PIL is available
            if USE_PIL:
                try:
                    if os.path.exists("shogun_icon.png"):
                        logo_img = Image.open("shogun_icon.png")
                        logo_img = logo_img.resize((40, 40), Image.LANCZOS)
                        logo_tk = ImageTk.PhotoImage(logo_img)
                        logo_label = tk.Label(logo_frame, image=logo_tk, bg=self.bg_color)
                        logo_label.image = logo_tk  # Keep a reference
                        logo_label.pack(side=tk.LEFT, padx=(0, 10))
                except Exception:
                    pass  # No logo, continue without it

            # タイトル
            title_label = tk.Label(logo_frame, text="Shogun 接続設定",
                                font=(FONT, 18, "bold"),
                                bg=self.bg_color, fg=self.fg_color)
            title_label.pack(side=tk.LEFT)

            # IPアドレス設定 - 現代的なカードスタイル
            ip_frame = tk.Frame(main_frame, bg=self.card_color, padx=20, pady=20,
                                borderwidth=0, relief="flat")
            ip_frame.pack(fill=tk.X, pady=10)

            ip_label = tk.Label(ip_frame, text="Shogun サーバーIPアドレス",
                            font=(FONT, 12, "bold"),
                            bg=self.card_color, fg=self.fg_color)
            ip_label.pack(anchor=tk.W, pady=(0, 10))

            ip_entry = tk.Entry(ip_frame, textvariable=self.ip_address,
                            font=(FONT, 12), width=30,
                            bg=self.bg_color, fg=self.fg_color,
                            insertbackground=self.fg_color)  # Cursor color
            ip_entry.pack(fill=tk.X, pady=5, ipady=8)  # Add internal padding for height

            ip_hint = tk.Label(ip_frame, text="例: 192.168.1.100 または localhost",
                            font=(FONT, 11),
                            bg=self.card_color, fg=self.accent_color)
            ip_hint.pack(anchor=tk.W, padx=5, pady=(5, 0))

            # CSVファイル選択 - 現代的なカードスタイル
            csv_frame = tk.Frame(main_frame, bg=self.card_color, padx=20, pady=20,
                                borderwidth=0, relief="flat")
            csv_frame.pack(fill=tk.X, pady=15)

            csv_label = tk.Label(csv_frame, text="キャプチャ名リストCSVファイル",
                                font=(FONT, 12, "bold"),
                                bg=self.card_color, fg=self.fg_color)
            csv_label.pack(anchor=tk.W, pady=(0, 10))

            csv_entry_frame = tk.Frame(csv_frame, bg=self.card_color)
            csv_entry_frame.pack(fill=tk.X, pady=5)

            csv_entry = tk.Entry(csv_entry_frame, textvariable=self.csv_path,
                                font=(FONT, 12),
                                bg=self.bg_color, fg=self.fg_color,
                                insertbackground=self.fg_color)
            csv_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)

            # Modern style browse button
            browse_btn = tk.Button(csv_entry_frame, text="参照...",
                                font=(FONT, 10),
                                command=self.browse_csv,
                                bg=self.button_color, fg=self.fg_color,
                                relief="flat", padx=10, pady=5,
                                activebackground=self.accent_color,
                                activeforeground=self.bg_color)
            browse_btn.pack(side=tk.LEFT, padx=(10, 0))

            csv_hint = tk.Label(csv_frame, text="CSVファイルの各行の最初の列がキャプチャ名として読み込まれます",
                            font=(FONT, 11),
                            bg=self.card_color, fg=self.accent_color)
            csv_hint.pack(anchor=tk.W, padx=5, pady=(5, 0))

            # ボタン - フラットでモダンなスタイル
            button_frame = tk.Frame(main_frame, bg=self.bg_color)
            button_frame.pack(fill=tk.X, pady=20)

            # Cancel button - outlined style
            cancel_button = tk.Button(button_frame, text="キャンセル",
                                    font=(FONT, 11),
                                    command=self.on_cancel,
                                    bg=self.bg_color, fg=self.fg_color,
                                    relief="flat", padx=15, pady=8,
                                    bd=1, highlightthickness=1,
                                    highlightbackground=self.fg_color,
                                    activebackground=self.bg_color)
            cancel_button.pack(side=tk.RIGHT, padx=5)

            # Connect button - filled style with accent color
            ok_button = tk.Button(button_frame, text="接続",
                                font=(FONT, 11, "bold"),
                                command=self.on_ok,
                                bg=self.accent_color, fg=self.bg_color,
                                relief="flat", padx=20, pady=8,
                                activebackground=self.accent_color,
                                activeforeground=self.bg_color)
            ok_button.pack(side=tk.RIGHT, padx=5)

            # Hover effects for buttons
            ModernWidget.create_hover_effect(
                cancel_button,
                self.button_color if self.is_dark_mode else "#E8E8E8",
                self.bg_color
            )

            # Subtle hover effect for the accent button
            ModernWidget.create_hover_effect(
                ok_button,
                "#7AC1CF" if self.is_dark_mode else "#6B2AE3",  # Slightly lighter/darker
                self.accent_color
            )

            # Enterキーでダイアログを確定
            self.dialog.bind("<Return>", lambda event: self.on_ok())
        except Exception as e:
            print(f"ウィジェット作成エラー: {str(e)}")
            traceback.print_exc()

    def browse_csv(self):
        """CSVファイル選択ダイアログを表示"""
        filepath = filedialog.askopenfilename(
            title="キャプチャ名リストCSVファイルを選択",
            filetypes=[("CSVファイル", "*.csv"), ("すべてのファイル", "*.*")]
        )
        if filepath:
            self.csv_path.set(filepath)

    def on_ok(self):
        """OKボタンが押されたときの処理"""
        ip = self.ip_address.get().strip()
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

        # 設定を保存
        self.save_settings()

        # 結果を返す
        self.result = {
            "ip_address": ip,
            "csv_path": csv_path
        }
        self.dialog.destroy()

    def on_cancel(self):
        """キャンセルボタンが押されたときの処理"""
        self.dialog.destroy()

class ShogunButtonUI:
    def __init__(self, root, ip_address="localhost", csv_file="Test.csv"):
        self.root = root
        self.root.title("Shogun Control Interface")
        self.root.geometry("1920x1080")

        # Theme setting
        self.is_dark_mode = True  # Default to dark mode

        # Set theme colors
        self.bg_color = DARK_BG if self.is_dark_mode else LIGHT_BG
        self.card_color = DARK_CARD if self.is_dark_mode else LIGHT_CARD
        self.accent_color = DARK_ACCENT if self.is_dark_mode else LIGHT_ACCENT
        self.text_color = DARK_TEXT if self.is_dark_mode else LIGHT_TEXT
        self.button_color = DARK_BUTTON if self.is_dark_mode else LIGHT_BUTTON

        # Configure root window with theme
        self.root.configure(bg=self.bg_color)

        # カウンタとキャプチャ名の変数を初期化
        self.count = 0
        self.capture_name_var = tk.StringVar(value="初期化中...")
        self.status_var = tk.StringVar(value="準備中...")
        self.count_var = tk.StringVar(value="0 / 0")
        self.capture_names = []

        # Progress animation canvas
        self.progress_canvas = None
        self.progress_animation_id = None

        # Shogun クライアント初期化
        self.connect_to_shogun(ip_address)

        # CSV ファイルからキャプチャ名リストを読み込む
        self.file = csv_file
        self.load_capture_names()

        # 初期キャプチャ名を設定
        if self.capture_names:
            try:
                self.ShogunCap.set_capture_name(self.capture_names[0])
            except Exception as e:
                print(f"初期キャプチャ名設定エラー: {str(e)}")

        # 音声認識コントローラー初期化
        self.voice_command_queue = queue.Queue()
        self.voice_controller = VoiceController(command_queue=self.voice_command_queue)
        self.voice_active = False
        self.voice_partial_var = tk.StringVar(value="")

        # UI作成
        self.create_ui()
        self.create_progress_canvas()

        # 音声コマンドのポーリング開始
        self._check_voice_commands()

    def connect_to_shogun(self, ip_address):
        """Shogunサーバーに接続する"""
        try:
            self.status_message = f"{ip_address}に接続中..."
            if hasattr(self, "root") and self.root:
                self.root.update_idletasks()

            self.c = Client(ip_address)
            self.ShogunCap = CaptureServices(self.c)
            self.ShogunPlay = PlaybackServices(self.c)
            self.ShogunSub = SubjectServices(self.c)

            self.status_message = f"{ip_address}に接続しました"
            print(f"Shogunサーバー {ip_address} に接続しました")
        except Exception as e:
            print(f"Shogun接続エラー: {str(e)}")
            messagebox.showerror("接続エラー", f"Shogunサーバーへの接続に失敗しました: {str(e)}")
            raise

    def load_capture_names(self):
        try:
            print(f"CSVファイル {self.file} からキャプチャ名を読み込みます")
            with open(self.file) as f:
                reader = csv.reader(f)
                self.capture_names = [row[0] for row in reader if row and len(row) > 0]
                print(f"読み込まれたキャプチャ名: {len(self.capture_names)}個")

            if not self.capture_names:
                print(f"警告: {self.file} にキャプチャ名が見つかりません")
                messagebox.showwarning("警告", f"{self.file}にキャプチャ名が見つかりません")
                self.capture_names = ["デフォルトキャプチャ"]  # デフォルト値を設定
        except Exception as e:
            print(f"CSVファイル読み込みエラー: {str(e)}")
            messagebox.showerror("エラー", f"CSVファイル読み込みエラー: {str(e)}")
            self.capture_names = ["デフォルトキャプチャ"]  # エラー時もデフォルト値を設定

    def create_ui(self):
        """UI全体を作成"""
        try:
            # Clear previous UI if any
            for widget in self.root.winfo_children():
                widget.destroy()

            # Create and configure root container with padding
            self.main_container = tk.Frame(self.root, bg=self.bg_color)
            self.main_container.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)

            # Configure grid layout
            self.main_container.columnconfigure(0, weight=1)
            self.main_container.rowconfigure(0, weight=0)  # Header
            self.main_container.rowconfigure(1, weight=1)  # Main content
            self.main_container.rowconfigure(2, weight=0)  # Status bar

            # Create header
            self.create_header()

            # Create main content area
            self.content_frame = tk.Frame(self.main_container, bg=self.bg_color)
            self.content_frame.grid(row=1, column=0, sticky="nsew", pady=20)

            # Configure content grid
            self.content_frame.columnconfigure(0, weight=1)
            self.content_frame.rowconfigure(0, weight=1)  # Capture control
            self.content_frame.rowconfigure(1, weight=1)  # Capture name selection
            self.content_frame.rowconfigure(2, weight=1)  # Subject control

            # Create card panels
            self.create_capture_control_panel()
            self.create_capture_name_panel()
            self.create_subject_control_panel()

            # Create status bar
            self.create_status_bar()

            # 初期状態を更新
            self.update_capture_name_display()
            self.update_status("準備完了", "success")

        except Exception as e:
            print(f"UI作成エラー: {str(e)}")
            traceback.print_exc()

    def create_progress_canvas(self):
        """Create a canvas for animated progress indicator"""
        try:
            self.progress_canvas = tk.Canvas(self.root, width=100, height=100,
                                        bg=self.bg_color, highlightthickness=0)
            self.progress_canvas.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
            self.progress_canvas.place_forget()  # Hide initially
        except Exception as e:
            print(f"進捗キャンバス作成エラー: {str(e)}")

    def show_progress_animation(self, show=True):
        """Show or hide circular progress animation"""
        if not hasattr(self, "progress_canvas") or self.progress_canvas is None:
            return

        try:
            if show:
                self.progress_canvas.config(bg=self.bg_color)
                self.progress_canvas.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
                self.animate_progress(0)
            else:
                if self.progress_animation_id:
                    self.progress_canvas.after_cancel(self.progress_animation_id)
                    self.progress_animation_id = None
                self.progress_canvas.place_forget()
        except Exception as e:
            print(f"進捗アニメーション表示エラー: {str(e)}")

    def animate_progress(self, angle):
        """Animate a circular progress indicator"""
        if not hasattr(self, "progress_canvas") or self.progress_canvas is None:
            return

        try:
            self.progress_canvas.delete("progress")

            # Draw arc
            self.progress_canvas.create_arc(20, 20, 80, 80,
                                        start=angle, extent=60,
                                        tags="progress",
                                        outline=self.accent_color, width=5, style="arc")

            # Continue animation
            angle = (angle + 10) % 360
            self.progress_animation_id = self.progress_canvas.after(40,
                                                                lambda: self.animate_progress(angle))
        except Exception as e:
            print(f"進捗アニメーションエラー: {str(e)}")

    def create_header(self):
        """Create modern app header with title and controls"""
        try:
            # Outer header container
            outer_header = tk.Frame(self.main_container, bg=self.bg_color)
            outer_header.grid(row=0, column=0, sticky="ew")

            header_frame = tk.Frame(outer_header, bg=self.bg_color, height=70)
            header_frame.pack(fill=tk.X)

            # App icon / logo
            if USE_PIL:
                try:
                    if os.path.exists("shogun_icon.png"):
                        logo_img = Image.open("shogun_icon.png")
                        logo_img = logo_img.resize((44, 44), Image.LANCZOS)
                        logo_tk = ImageTk.PhotoImage(logo_img)
                        logo_label = tk.Label(header_frame, image=logo_tk, bg=self.bg_color)
                        logo_label.image = logo_tk
                        logo_label.pack(side=tk.LEFT, padx=(0, 12))
                except Exception:
                    pass

            # App title block
            title_block = tk.Frame(header_frame, bg=self.bg_color)
            title_block.pack(side=tk.LEFT)

            title_label = tk.Label(title_block, text="Shogun Control",
                                font=(FONT, 22, "bold"),
                                bg=self.bg_color, fg=self.text_color)
            title_label.pack(anchor=tk.W)

            subtitle_label = tk.Label(title_block, text="Motion Capture Interface",
                                   font=(FONT, 10),
                                   bg=self.bg_color, fg=DARK_MUTED if self.is_dark_mode else LIGHT_MUTED)
            subtitle_label.pack(anchor=tk.W)

            # Vertical divider
            divider = tk.Frame(header_frame, bg=self.accent_color, width=2)
            divider.pack(side=tk.LEFT, fill=tk.Y, padx=20, pady=8)

            # Capture name block (center-left)
            name_block = tk.Frame(header_frame, bg=self.bg_color)
            name_block.pack(side=tk.LEFT)

            cap_hint = tk.Label(name_block, text="CURRENT CAPTURE",
                              font=(FONT, 9, "bold"),
                              bg=self.bg_color, fg=DARK_MUTED if self.is_dark_mode else LIGHT_MUTED)
            cap_hint.pack(anchor=tk.W)

            name_display = tk.Label(name_block, textvariable=self.capture_name_var,
                                font=(FONT, 18, "bold"),
                                bg=self.bg_color, fg=self.accent_color)
            name_display.pack(anchor=tk.W)

            # Right side controls
            controls_frame = tk.Frame(header_frame, bg=self.bg_color)
            controls_frame.pack(side=tk.RIGHT, padx=(0, 5))

            ctrl_btn_style = dict(
                font=(FONT, 15), bg=self.bg_color, fg=self.text_color,
                bd=0, relief="flat", padx=10, pady=6,
                activebackground=self.button_color, cursor="hand2"
            )

            help_btn = tk.Button(controls_frame, text="?", command=self.show_help, **ctrl_btn_style)
            help_btn.pack(side=tk.RIGHT, padx=3)

            theme_icon = "◐" if self.is_dark_mode else "◑"
            theme_btn = tk.Button(controls_frame, text=theme_icon, command=self.toggle_theme, **ctrl_btn_style)
            theme_btn.pack(side=tk.RIGHT, padx=3)

            settings_btn = tk.Button(controls_frame, text="⚙", command=self.open_settings, **ctrl_btn_style)
            settings_btn.pack(side=tk.RIGHT, padx=3)

            # マイクボタン（音声認識が利用可能な場合のみ表示）
            if self.voice_controller.is_available():
                mic_color = DARK_SUCCESS if self.voice_active else self.bg_color
                mic_fg = DARK_BG if self.voice_active else self.text_color
                self.mic_btn = tk.Button(
                    controls_frame, text="🎤",
                    command=self.toggle_voice,
                    font=(FONT, 15), bg=mic_color, fg=mic_fg,
                    bd=0, relief="flat", padx=10, pady=6,
                    activebackground=self.button_color, cursor="hand2"
                )
                self.mic_btn.pack(side=tk.RIGHT, padx=3)

            # Accent separator line below header
            sep = tk.Frame(outer_header, bg=self.accent_color, height=2)
            sep.pack(fill=tk.X)

            # 音声認識の部分認識テキスト表示（ヘッダー下）
            if self.voice_controller.is_available():
                self.voice_feedback_frame = tk.Frame(outer_header, bg=self.bg_color, height=24)
                self.voice_feedback_frame.pack(fill=tk.X)
                self.voice_feedback_label = tk.Label(
                    self.voice_feedback_frame, textvariable=self.voice_partial_var,
                    font=(FONT, 10), bg=self.bg_color,
                    fg=DARK_MUTED if self.is_dark_mode else LIGHT_MUTED,
                    anchor=tk.W
                )
                self.voice_feedback_label.pack(side=tk.LEFT, padx=15)

        except Exception as e:
            print(f"ヘッダー作成エラー: {str(e)}")
            traceback.print_exc()

    def create_card(self, parent, title, height=None):
        """Helper to create consistent card panels with accent left border"""
        try:
            # Wrapper: accent color peeks through as 4px left border
            wrapper = tk.Frame(parent, bg=self.accent_color, bd=0)
            if height:
                wrapper.configure(height=height)

            # Inner card offset 4px from left → exposes accent border
            card = tk.Frame(wrapper, bg=self.card_color, padx=22, pady=18,
                        relief="flat", bd=0)
            card.pack(fill=tk.BOTH, expand=True, padx=(4, 0))

            # Card title
            title_label = tk.Label(card, text=title, font=(FONT, 14, "bold"),
                                bg=self.card_color, fg=self.text_color)
            title_label.pack(anchor=tk.W, pady=(0, 6))

            # Subtle separator line under title
            sep = tk.Frame(card, bg=self.accent_color, height=1)
            sep.pack(fill=tk.X, pady=(0, 12))

            # Content frame
            content = tk.Frame(card, bg=self.card_color)
            content.pack(fill=tk.BOTH, expand=True)

            return wrapper, content
        except Exception as e:
            print(f"カード作成エラー: {str(e)}")
            fallback_frame = tk.Frame(parent, bg=self.card_color, padx=10, pady=10)
            fallback_content = tk.Frame(fallback_frame, bg=self.card_color)
            fallback_content.pack(fill=tk.BOTH, expand=True)
            return fallback_frame, fallback_content

    def create_capture_control_panel(self):
        """Create capture control buttons panel"""
        try:
            capture_frame = tk.Frame(self.content_frame, bg=self.bg_color)
            capture_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 20))

            card, content = self.create_card(capture_frame, "キャプチャ操作")
            card.pack(fill=tk.BOTH, expand=True)

            btn_frame = tk.Frame(content, bg=self.card_color)
            btn_frame.pack(fill=tk.BOTH, expand=True)
            for i in range(4):
                btn_frame.columnconfigure(i, weight=1)

            # Button configs: (label, command, bg_normal, fg, bg_hover)
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
                                activebackground=hover_bg,
                                activeforeground=fg,
                                command=cmd,
                                relief="flat", bd=0,
                                pady=18, padx=8,
                                cursor="hand2")
                btn.grid(row=0, column=col, padx=8, pady=6, sticky="nsew")
                ModernWidget.create_hover_effect(btn, hover_bg, bg)

        except Exception as e:
            print(f"キャプチャ操作パネル作成エラー: {str(e)}")
            traceback.print_exc()

    def create_capture_name_panel(self):
        """Create capture name navigation panel"""
        try:
            name_frame = tk.Frame(self.content_frame, bg=self.bg_color)
            name_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 20))

            card, content = self.create_card(name_frame, "キャプチャ名選択")
            card.pack(fill=tk.BOTH, expand=True)

            nav_container = tk.Frame(content, bg=self.card_color)
            nav_container.pack(fill=tk.BOTH, expand=True)
            nav_container.columnconfigure(0, weight=1)
            nav_container.columnconfigure(1, weight=3)
            nav_container.columnconfigure(2, weight=1)

            nav_btn_style = dict(
                font=(FONT, 20, "bold"),
                bg=self.button_color, fg=self.accent_color,
                relief="flat", bd=0, padx=16, pady=12, cursor="hand2",
                activebackground=self.accent_color, activeforeground=self.bg_color
            )

            back_btn = tk.Button(nav_container, text="❮", command=self.back_capture_name, **nav_btn_style)
            back_btn.grid(row=0, column=0, padx=(0, 10), pady=8, sticky="nsew")
            ModernWidget.create_hover_effect(back_btn, self.accent_color, self.button_color)

            # Center: count display
            count_frame = tk.Frame(nav_container, bg=self.card_color)
            count_frame.grid(row=0, column=1, padx=10, pady=8, sticky="nsew")

            self.update_count_display()

            count_label = tk.Label(count_frame, textvariable=self.count_var,
                                  font=(FONT, 20, "bold"),
                                  bg=self.card_color, fg=self.text_color)
            count_label.pack(expand=True)

            of_label = tk.Label(count_frame, text="テイク",
                               font=(FONT, 10),
                               bg=self.card_color,
                               fg=DARK_MUTED if self.is_dark_mode else LIGHT_MUTED)
            of_label.pack()

            next_btn = tk.Button(nav_container, text="❯", command=self.next_capture_name, **nav_btn_style)
            next_btn.grid(row=0, column=2, padx=(10, 0), pady=8, sticky="nsew")
            ModernWidget.create_hover_effect(next_btn, self.accent_color, self.button_color)

        except Exception as e:
            print(f"キャプチャ名パネル作成エラー: {str(e)}")
            traceback.print_exc()

    def update_count_display(self):
        """Update the capture name count display"""
        try:
            if self.capture_names:
                self.count_var.set(f"{self.count + 1} / {len(self.capture_names)}")
            else:
                self.count_var.set("0 / 0")
        except Exception as e:
            print(f"カウント表示更新エラー: {str(e)}")

    def create_subject_control_panel(self):
        """Create subject control panel"""
        try:
            subject_frame = tk.Frame(self.content_frame, bg=self.bg_color)
            subject_frame.grid(row=2, column=0, sticky="nsew")

            card, content = self.create_card(subject_frame, "サブジェクト操作")
            card.pack(fill=tk.BOTH, expand=True)

            reboot_color = DARK_ERROR if self.is_dark_mode else LIGHT_ERROR

            reboot_btn = tk.Button(content, text="⟳  全サブジェクト再起動",
                                  font=(FONT, 14, "bold"),
                                  bg=reboot_color, fg="#1E1E2E",
                                  command=self.all_subject_reboot,
                                  relief="flat", bd=0, padx=20, pady=16,
                                  cursor="hand2",
                                  activebackground=self._dim_color(reboot_color),
                                  activeforeground="#1E1E2E")
            reboot_btn.pack(expand=True, padx=40, pady=15, fill=tk.X)
            ModernWidget.create_hover_effect(reboot_btn, self._dim_color(reboot_color), reboot_color)

        except Exception as e:
            print(f"サブジェクト操作パネル作成エラー: {str(e)}")
            traceback.print_exc()

    def create_status_bar(self):
        """Create modern status bar with pulsing indicator"""
        try:
            # Top separator line
            sep = tk.Frame(self.main_container, bg=self.button_color, height=1)
            sep.grid(row=2, column=0, sticky="ew")

            status_frame = tk.Frame(self.main_container,
                                   bg=DARK_SURFACE if self.is_dark_mode else "#EBEBF5",
                                   height=40)
            status_frame.grid(row=3, column=0, sticky="ew")
            self.main_container.rowconfigure(3, weight=0)

            # Pulsing status dot
            self._status_dot_color = "#4CAF50"
            self._status_dot_bright = True
            self.status_dot = tk.Label(status_frame, text="⬤", font=(FONT, 10),
                                      bg=DARK_SURFACE if self.is_dark_mode else "#EBEBF5",
                                      fg=self._status_dot_color)
            self.status_dot.pack(side=tk.LEFT, padx=(15, 6))

            status_label = tk.Label(status_frame, textvariable=self.status_var,
                                   font=(FONT, 11),
                                   bg=DARK_SURFACE if self.is_dark_mode else "#EBEBF5",
                                   fg=self.text_color)
            status_label.pack(side=tk.LEFT)

            # Right side: version badge
            version_frame = tk.Frame(status_frame, bg=self.accent_color, padx=8, pady=2)
            version_frame.pack(side=tk.RIGHT, padx=15, pady=6)
            tk.Label(version_frame, text="v7.0", font=(FONT, 9, "bold"),
                    bg=self.accent_color, fg=DARK_BG).pack()

            # Start pulse animation
            self._pulse_status()

        except Exception as e:
            print(f"ステータスバー作成エラー: {str(e)}")
            traceback.print_exc()

    def _pulse_status(self):
        """Animate the status dot with a gentle pulse"""
        if not hasattr(self, "status_dot") or not self.status_dot.winfo_exists():
            return
        try:
            self._status_dot_bright = not self._status_dot_bright
            dot_color = self._status_dot_color if self._status_dot_bright else self._dim_color(self._status_dot_color)
            self.status_dot.configure(fg=dot_color)
            self.root.after(700, self._pulse_status)
        except Exception:
            pass

    def _dim_color(self, hex_color):
        """Return a dimmed version of a hex color (45% brightness)"""
        try:
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            r, g, b = int(r * 0.45), int(g * 0.45), int(b * 0.45)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color

    def update_capture_name_display(self):
        """Update the displayed capture name"""
        try:
            if 0 <= self.count < len(self.capture_names):
                self.capture_name_var.set(self.capture_names[self.count])
            else:
                self.capture_name_var.set("無効なインデックス")

            # Also update count display
            self.update_count_display()
        except Exception as e:
            print(f"キャプチャ名表示更新エラー: {str(e)}")

    def start_capture(self):
        """Start motion capture"""
        try:
            self.update_status("キャプチャを開始しています...", "processing")

            # Show progress animation
            self.show_progress_animation(True)

            # Start capture
            self.ShogunCap.start_capture()

            # Hide progress animation after delay
            self.root.after(500, lambda: self.show_progress_animation(False))

            self.update_status("キャプチャを開始しました", "success")
        except Exception as e:
            self.show_progress_animation(False)
            self.update_status(f"エラー: {str(e)}", "error")
            messagebox.showerror("エラー", f"キャプチャ開始エラー: {str(e)}")

    def stop_capture(self):
        """Stop motion capture"""
        try:
            self.update_status("キャプチャを停止しています...", "processing")

            # Show progress animation
            self.show_progress_animation(True)

            # Stop capture
            self.ShogunCap.stop_capture(0)

            # Hide progress animation after delay
            self.root.after(500, lambda: self.show_progress_animation(False))

            self.update_status("キャプチャを停止しました", "success")
        except Exception as e:
            self.show_progress_animation(False)
            self.update_status(f"エラー: {str(e)}", "error")
            messagebox.showerror("エラー", f"キャプチャ停止エラー: {str(e)}")

    def enter_playback(self):
        """Enter review mode"""
        try:
            self.update_status("レビューを開始しています...", "processing")

            # Show progress animation
            self.show_progress_animation(True)

            # Enter review mode
            capture_list = self.ShogunCap.latest_capture_name()

            # レスポンスの確認
            if not capture_list or len(capture_list) < 3:
                raise ValueError("キャプチャリストが正しくありません")

            self.ShogunPlay.enter_capture_review(capture_list[2])
            self.ShogunPlay.play()

            # Hide progress animation after delay
            self.root.after(500, lambda: self.show_progress_animation(False))

            self.update_status(f"レビュー中: {capture_list[2]}", "success")
        except Exception as e:
            self.show_progress_animation(False)
            self.update_status(f"エラー: {str(e)}", "error")
            messagebox.showerror("エラー", f"レビュー開始エラー: {str(e)}")

    def exit_review(self):
        """Exit review mode"""
        try:
            self.update_status("レビューを終了しています...", "processing")

            # Show progress animation
            self.show_progress_animation(True)

            # Exit review mode
            self.ShogunPlay.exit_review()

            # Hide progress animation after delay
            self.root.after(500, lambda: self.show_progress_animation(False))

            self.update_status("レビューを終了しました", "success")
        except Exception as e:
            self.show_progress_animation(False)
            self.update_status(f"エラー: {str(e)}", "error")
            messagebox.showerror("エラー", f"レビュー終了エラー: {str(e)}")

    def next_capture_name(self):
        """Move to next capture name in the list"""
        try:
            if self.count < len(self.capture_names) - 1:
                self.count += 1
                self.update_capture_name()
            else:
                self.update_status("リストの最後に到達しました", "info")
                messagebox.showinfo("情報", "リストの最後に到達しました")
        except Exception as e:
            self.update_status(f"エラー: {str(e)}", "error")
            print(f"次キャプチャ名エラー: {str(e)}")

    def back_capture_name(self):
        """Move to previous capture name in the list"""
        try:
            if self.count > 0:
                self.count -= 1
                self.update_capture_name()
            else:
                self.update_status("リストの先頭に到達しました", "info")
                messagebox.showinfo("情報", "リストの先頭に到達しました")
        except Exception as e:
            self.update_status(f"エラー: {str(e)}", "error")
            print(f"前キャプチャ名エラー: {str(e)}")

    def update_capture_name(self):
        """Update the active capture name"""
        try:
            if 0 <= self.count < len(self.capture_names):
                self.ShogunCap.set_capture_name(self.capture_names[self.count])
                self.update_capture_name_display()
                self.update_status(f"キャプチャ名を変更: {self.capture_names[self.count]}", "info")
        except Exception as e:
            self.update_status(f"エラー: {str(e)}", "error")
            messagebox.showerror("エラー", f"キャプチャ名更新エラー: {str(e)}")

    def all_subject_reboot(self):
        """Reboot all subjects"""
        try:
            self.update_status("サブジェクト再起動中...", "processing")

            # Show progress animation
            self.show_progress_animation(True)

            # Reboot subjects
            self.ShogunSub.set_all_subjects_enabled(False)
            self.root.after(500)  # 少し待機
            self.ShogunSub.set_all_subjects_enabled(True)

            # Hide progress animation after delay
            self.root.after(1000, lambda: self.show_progress_animation(False))

            self.update_status("全サブジェクトを再起動しました", "success")
        except Exception as e:
            self.show_progress_animation(False)
            self.update_status(f"エラー: {str(e)}", "error")
            messagebox.showerror("エラー", f"サブジェクト再起動エラー: {str(e)}")

    def update_status(self, message, status_type="info"):
        """Update status bar with message and appropriate color"""
        try:
            self.status_var.set(message)

            color_map = {
                "success":    "#4CAF50",
                "error":      "#F44336",
                "processing": "#FFC107",
                "info":       "#2196F3",
            }
            new_color = color_map.get(status_type, "#2196F3")
            self._status_dot_color = new_color

            if hasattr(self, "status_dot") and self.status_dot.winfo_exists():
                self.status_dot.configure(fg=new_color)

            print(f"ステータス: {message}")
            self.root.update()
        except Exception as e:
            print(f"ステータス更新エラー: {str(e)}")

    def toggle_voice(self):
        """音声認識のON/OFFを切り替え"""
        try:
            if self.voice_active:
                self.voice_controller.stop()
                self.voice_active = False
                self.voice_partial_var.set("")
                self.update_status("音声認識を無効にしました", "info")
            else:
                if self.voice_controller.start():
                    self.voice_active = True
                    self.update_status("音声認識を有効にしました - コマンドを話してください", "success")
                else:
                    self.update_status("音声認識の開始に失敗しました", "error")
                    return

            # マイクボタンの見た目を更新
            if hasattr(self, "mic_btn") and self.mic_btn.winfo_exists():
                if self.voice_active:
                    self.mic_btn.configure(
                        bg=DARK_SUCCESS if self.is_dark_mode else LIGHT_SUCCESS,
                        fg=DARK_BG
                    )
                else:
                    self.mic_btn.configure(bg=self.bg_color, fg=self.text_color)
        except Exception as e:
            self.update_status(f"音声認識エラー: {str(e)}", "error")
            print(f"音声認識トグルエラー: {str(e)}")

    def _check_voice_commands(self):
        """音声コマンドキューを定期的にチェック"""
        try:
            while not self.voice_command_queue.empty():
                msg_type, data = self.voice_command_queue.get_nowait()

                if msg_type == "command":
                    self.voice_partial_var.set("")
                    self._execute_voice_command(data)
                elif msg_type == "partial":
                    self.voice_partial_var.set(f"🎤 {data}")
                elif msg_type == "unrecognized":
                    self.voice_partial_var.set(f"🎤 {data}")
                    # 2秒後にクリア
                    self.root.after(2000, lambda: self.voice_partial_var.set("")
                                   if self.voice_partial_var.get().endswith(data) else None)
        except Exception as e:
            print(f"音声コマンドチェックエラー: {str(e)}")

        # 100msごとにポーリング
        self.root.after(100, self._check_voice_commands)

    def _execute_voice_command(self, action):
        """音声コマンドを実行"""
        label = VOICE_COMMAND_LABELS.get(action, action)
        self.update_status(f"音声コマンド: {label}", "info")

        command_map = {
            "start_capture": self.start_capture,
            "stop_capture": self.stop_capture,
            "enter_playback": self.enter_playback,
            "exit_review": self.exit_review,
            "next_capture": self.next_capture_name,
            "back_capture": self.back_capture_name,
            "reboot_subjects": self.all_subject_reboot,
        }

        func = command_map.get(action)
        if func:
            try:
                func()
            except Exception as e:
                self.update_status(f"コマンド実行エラー: {str(e)}", "error")

    def toggle_theme(self):
        """Toggle between light and dark mode"""
        try:
            self.is_dark_mode = not self.is_dark_mode

            # Update colors
            self.bg_color = DARK_BG if self.is_dark_mode else LIGHT_BG
            self.card_color = DARK_CARD if self.is_dark_mode else LIGHT_CARD
            self.accent_color = DARK_ACCENT if self.is_dark_mode else LIGHT_ACCENT
            self.text_color = DARK_TEXT if self.is_dark_mode else LIGHT_TEXT
            self.button_color = DARK_BUTTON if self.is_dark_mode else LIGHT_BUTTON

            # Recreate UI with new theme
            self.create_ui()
        except Exception as e:
            print(f"テーマ切替エラー: {str(e)}")
            messagebox.showerror("エラー", f"テーマの切り替えに失敗しました: {str(e)}")

    def open_settings(self):
        """Open settings dialog"""
        try:
            settings_dialog = SettingsDialog(self.root, self.is_dark_mode)
            self.root.wait_window(settings_dialog.dialog)

            # キャンセルされた場合は何もしない
            if not settings_dialog.result:
                return

            # 新しい設定を適用
            try:
                # Show progress animation during reconnection
                self.show_progress_animation(True)

                # 現在のクライアントを閉じる
                try:
                    self.c.disconnect()
                except Exception as e:
                    print(f"切断エラー（無視します）: {str(e)}")

                # 新しい設定で再接続
                new_ip = settings_dialog.result["ip_address"]
                new_csv = settings_dialog.result["csv_path"]

                # 接続とCSVロード
                self.connect_to_shogun(new_ip)
                self.file = new_csv
                self.load_capture_names()

                # Hide progress animation
                self.show_progress_animation(False)

                # 最初のキャプチャ名をセット
                if self.capture_names:
                    self.count = 0
                    self.ShogunCap.set_capture_name(self.capture_names[0])
                    self.update_capture_name_display()

                self.update_status(f"新しい設定を適用: サーバー {new_ip}, CSV {os.path.basename(new_csv)}", "success")
                messagebox.showinfo("設定更新", "新しい接続設定が適用されました")

            except Exception as e:
                self.show_progress_animation(False)
                self.update_status(f"設定エラー: {str(e)}", "error")
                messagebox.showerror("設定エラー", f"新しい設定の適用に失敗しました: {str(e)}")
        except Exception as e:
            print(f"設定ダイアログエラー: {str(e)}")
            traceback.print_exc()

    def show_help(self):
        """Show help dialog with modern styling"""
        try:
            help_dialog = tk.Toplevel(self.root)
            help_dialog.title("使い方")
            help_dialog.geometry("650x700")
            help_dialog.configure(bg=self.bg_color)

            # Center on parent
            help_dialog.transient(self.root)
            help_dialog.update_idletasks()
            x = self.root.winfo_x() + (self.root.winfo_width() - 650) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - 500) // 2
            help_dialog.geometry(f"+{x}+{y}")

            # Create content frame with padding
            content = tk.Frame(help_dialog, bg=self.bg_color, padx=30, pady=30)
            content.pack(fill=tk.BOTH, expand=True)

            # Title
            title = tk.Label(content, text="Shogun Control インターフェースの使い方",
                           font=(FONT, 18, "bold"),
                           bg=self.bg_color, fg=self.text_color)
            title.pack(anchor="w", pady=(0, 20))

            # Help sections in cards
            sections = [
                ("キャプチャ操作", [
                    "キャプチャ開始: モーションキャプチャを開始します",
                    "キャプチャ停止: 現在のキャプチャを停止します",
                    "レビュー開始: 最後のキャプチャのレビューを開始します",
                    "レビュー終了: レビューモードを終了します"
                ]),
                ("キャプチャ名選択", [
                    "前/次のキャプチャ名: CSVファイルから読み込まれたキャプチャ名リストを移動します",
                    "現在のキャプチャ名はアプリの上部に常に表示されます"
                ]),
                ("サブジェクト操作", [
                    "全サブジェクト再起動: すべてのサブジェクトを無効化し、再度有効化します",
                    "この操作は注意して使用してください"
                ]),
                ("音声認識操作", [
                    "🎤ボタンで音声認識のON/OFFを切り替えます",
                    "「レック」「開始」: キャプチャ開始",
                    "「カット」「停止」: キャプチャ停止",
                    "「レビュー」「再生」: レビュー開始",
                    "「レビュー終了」: レビュー終了",
                    "「次」「次へ」: 次のキャプチャ名",
                    "「前」「前へ」: 前のキャプチャ名",
                    "「リブート」「再起動」: サブジェクト再起動",
                ]),
                ("その他の機能", [
                    "設定: 接続先サーバーとCSVファイルを変更できます",
                    "テーマ切替: ダークモードとライトモードを切り替えられます",
                    "状態表示: 画面下部のステータスバーで現在の状態を確認できます"
                ])
            ]

            # Create simple help sections
            for i, (section_title, items) in enumerate(sections):
                section_frame = tk.Frame(content, bg=self.card_color, padx=20, pady=15)
                section_frame.pack(fill=tk.X, pady=10)

                # Section title
                section_label = tk.Label(section_frame, text=section_title,
                                       font=(FONT, 14, "bold"),
                                       bg=self.card_color, fg=self.accent_color)
                section_label.pack(anchor="w", pady=(0, 10))

                # Section items
                for item in items:
                    item_frame = tk.Frame(section_frame, bg=self.card_color)
                    item_frame.pack(fill=tk.X, pady=3)

                    bullet = tk.Label(item_frame, text="•", font=(FONT, 12, "bold"),
                                    bg=self.card_color, fg=self.accent_color)
                    bullet.pack(side=tk.LEFT, padx=(0, 8))

                    text = tk.Label(item_frame, text=item, font=(FONT, 12),
                                  bg=self.card_color, fg=self.text_color,
                                  justify=tk.LEFT, wraplength=550)
                    text.pack(side=tk.LEFT, fill=tk.X)

            # Close button at bottom
            close_btn = tk.Button(content, text="閉じる", font=(FONT, 12, "bold"),
                                bg=self.accent_color, fg=self.bg_color,
                                command=help_dialog.destroy,
                                relief="raised", borderwidth=2, padx=20, pady=8)
            close_btn.pack(pady=20)

            # Grab focus
            help_dialog.focus_set()
        except Exception as e:
            print(f"ヘルプ表示エラー: {str(e)}")
            messagebox.showerror("エラー", f"ヘルプの表示に失敗しました: {str(e)}")

def main():
    """メイン関数 - アプリケーションの開始ポイント"""
    try:
        # エラーログをファイルに記録する設定
        if not os.path.exists("logs"):
            os.makedirs("logs")
        log_file = os.path.join("logs", f"shogun_control_log_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        print(f"ログファイル: {log_file}")

        # メインウィンドウを作成
        root = tk.Tk()
        root.title("Shogun Control")

        # エラーログリダイレクト（オプション）
        try:
            # 標準エラー出力をファイルにリダイレクト
            sys.stderr = open(log_file, 'w')
            print("エラーログをリダイレクトしました")
        except Exception as e:
            print(f"ログリダイレクトエラー: {str(e)}")

        # シンプルなスプラッシュスクリーン
        def show_simple_splash():
            splash = tk.Toplevel(root)
            splash.title("")
            splash.overrideredirect(True)  # No window decorations

            splash_width, splash_height = 400, 200
            splash.geometry(f"{splash_width}x{splash_height}")
            splash.configure(bg=DARK_BG)

            # Center splash
            screen_width = root.winfo_screenwidth()
            screen_height = root.winfo_screenheight()
            x = (screen_width - splash_width) // 2
            y = (screen_height - splash_height) // 2
            splash.geometry(f"{splash_width}x{splash_height}+{x}+{y}")

            # Simple content
            label = tk.Label(splash, text="Shogun Control",
                           font=(FONT, 24, "bold"),
                           bg=DARK_BG, fg=DARK_TEXT)
            label.pack(pady=(50, 10))

            loading = tk.Label(splash, text="起動中...",
                             font=(FONT, 12),
                             bg=DARK_BG, fg=DARK_ACCENT)
            loading.pack()

            # Make sure splash is on top
            splash.attributes('-topmost', True)
            splash.update()

            # Close splash after delay
            root.after(1500, lambda: (splash.destroy(), start_app()))

            return splash

        # Actual application start function
        def start_app():
            try:
                # アプリケーションアイコンを設定（オプション）
                try:
                    # Windows
                    if os.path.exists("shogun_icon.ico"):
                        root.iconbitmap("shogun_icon.ico")
                except Exception as e:
                    print(f"アイコン設定エラー（無視します）: {str(e)}")

                # スクリーンサイズを取得
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                print(f"画面サイズ: {screen_width}x{screen_height}")

                # 設定ダイアログを作成
                print("設定ダイアログを作成します...")
                settings_dialog = SettingsDialog(root, is_dark_mode=True)  # デフォルトはダークモード

                # Show root window now with appropriate size
                if screen_width >= 1920 and screen_height >= 1080:
                    root.geometry("1600x900+0+0")  # フルHDサイズに合わせて少し小さく
                else:
                    # 画面解像度が低い場合は画面の80%のサイズに調整
                    width = int(screen_width * 0.8)
                    height = int(screen_height * 0.8)
                    x = (screen_width - width) // 2
                    y = (screen_height - height) // 2
                    root.geometry(f"{width}x{height}+{x}+{y}")

                root.deiconify()

                # ダイアログが閉じるまで待機
                print("設定ダイアログの完了を待機中...")
                root.wait_window(settings_dialog.dialog)
                print("設定ダイアログが閉じられました")

                # 設定がキャンセルされた場合は終了
                if not settings_dialog.result:
                    print("設定がキャンセルされました。アプリケーションを終了します。")
                    root.destroy()
                    return

                # 設定を取得
                ip_address = settings_dialog.result["ip_address"]
                csv_path = settings_dialog.result["csv_path"]
                print(f"接続設定: IP={ip_address}, CSV={csv_path}")

                # メインアプリケーションを作成
                try:
                    print("メインアプリケーションを初期化中...")
                    app = ShogunButtonUI(root, ip_address, csv_path)
                    print("初期化完了、アプリケーションを起動します")

                except Exception as e:
                    # 例外をキャッチしてメッセージボックスを表示
                    error_message = f"アプリケーション実行中にエラーが発生しました:\n{str(e)}\n\n"
                    error_message += traceback.format_exc()

                    # エラーログをファイルに書き込む
                    try:
                        with open(log_file, "a") as f:
                            f.write(error_message)
                    except:
                        pass  # ログファイル書き込みエラーは無視

                    messagebox.showerror("エラー", f"エラーが発生しました。詳細はログファイルを確認してください。\n{str(e)}")
                    print(error_message)
            except Exception as e:
                # start_app関数内のエラー
                error_message = f"アプリケーション起動中にエラーが発生しました:\n{str(e)}\n\n"
                error_message += traceback.format_exc()
                print(error_message)

                root.deiconify()  # 必ずウィンドウを表示
                messagebox.showerror("エラー", f"アプリケーションの起動に失敗しました: {str(e)}")
                root.destroy()

        # スプラッシュを表示してイベントループを起動
        root.withdraw()
        show_simple_splash()
        root.mainloop()

    except Exception as e:
        # メイン関数レベルでのエラー処理
        error_message = f"初期化中にエラーが発生しました:\n{str(e)}\n\n"
        error_message += traceback.format_exc()

        # コンソールに出力
        print(error_message)

        # メッセージボックスが使える場合は表示
        try:
            messagebox.showerror("致命的なエラー", f"アプリケーションの起動に失敗しました。\n{str(e)}")
        except:
            # GUIが初期化されていない場合は標準のウィンドウを表示
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, f"アプリケーションの起動に失敗しました:\n{error_message}", "致命的なエラー", 0)
            except:
                # 全ての方法が失敗した場合はコンソール出力のみ
                print("GUI表示に失敗しました。コンソールログを確認してください。")

# アプリケーションが直接実行された場合のエントリーポイント
if __name__ == "__main__":
    main()
