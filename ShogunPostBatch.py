"""
ShogunPostBatch.py
==================
指定フォルダ内の X2D ファイルを ShogunPostCL.exe（ヘッドレス）で
バッチ再計算（Post Process）し、X2D と同じ場所に同名でエクスポートするツール。
処理は Offline.QuickPost、保存は ExportFile を使う（既定 .fbx）。

重要（実機検証で判明した制約）:
  - HDF のネイティブ保存(SaveFile)はヘッドレスで ShogunPostCL がクラッシュするため不可。
    vdf エクスポータも未搭載。動作確認済みの出力は fbx / c3d / trc。
  - Reconstruct→AutoLabel→Solve を個別に順次呼ぶとクラッシュするため、
    QuickPost（x2d オフライン処理専用）に一本化している。

仕組み:
  1. インストール済みの ViconShogunPostSDK のバージョンに合う
     ShogunPostCL.exe をヘッドレス起動する。
  2. SDK（ViconShogunPost）でその ShogunPostCL に制御接続する
     （起動完了まで接続をリトライ）。
  3. 各 X2D について以下を実行:
       NewScene → ImportFile(x2d) →〔任意で VSK Import〕→
       Offline.QuickPost(level) → ExportFile(.fbx 等)
  4. 切断して ShogunPostCL を終了する。

前提・注意:
  - ViconShogunPostSDK が pip 導入済みであること
    （`import ViconShogunPost` が通ること）。Shogun Post 付属の
    SDK\\Win64\\install_vicon_shogun_post_sdk.bat で導入できる。
  - QuickPost(Label/Solve) はシーンにサブジェクトが必要。X2D に
    サブジェクトが含まれない場合は --subjects で VSK を指定するか、
    --level reconstruct で再構成のみ実行する。
  - SDK 制御ポート（既定 803）を使うため、同一マシンで対話用の
    Shogun Post を同時起動していると接続先が競合する可能性がある。
  - ShogunPostCL は実行中ライセンスを 1 つ消費する。

使用例:
  python ShogunPostBatch.py "D:/Capture/Day1/Session1"
  python ShogunPostBatch.py "D:/Capture" --recursive
  python ShogunPostBatch.py "D:/Capture" --level reconstruct
  python ShogunPostBatch.py "D:/Capture" --format c3d
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

VICON_DIR = r"C:\Program Files\Vicon"


def import_sdk():
    """ViconShogunPost SDK を読み込んで (ViconShogunPost, Offline) を返す。"""
    try:
        import ViconShogunPost
        from ViconShogunPostSDK import Offline
    except ImportError as e:
        print("エラー: ViconShogunPostSDK が見つかりません。")
        print("  Shogun Post 付属の SDK を導入してください:")
        print(r"  C:\Program Files\Vicon\ShogunPost1.x\SDK\Win64\install_vicon_shogun_post_sdk.bat")
        print(f"  詳細: {e}")
        sys.exit(1)
    return ViconShogunPost, Offline


def sdk_version():
    """導入済み ViconShogunPostSDK のバージョン文字列（例 '1.18.0'）を返す。"""
    try:
        import importlib.metadata as md
        return md.version("ViconShogunPostSDK")
    except Exception:
        return None


def _enumerate_cl():
    """インストール済み ShogunPostCL を {(maj,min): exe_path} で返す。"""
    found = {}
    if not os.path.isdir(VICON_DIR):
        return found
    import re
    for name in os.listdir(VICON_DIR):
        m = re.match(r"ShogunPost(\d+)\.(\d+)$", name)
        if m:
            exe = os.path.join(VICON_DIR, name, "ShogunPostCL.exe")
            if os.path.isfile(exe):
                found[(int(m.group(1)), int(m.group(2)))] = exe
    return found


def find_shogunpost_cl(explicit_path=None):
    """ShogunPostCL.exe のパスを返す。SDK と同じバージョンを優先する。"""
    if explicit_path:
        if os.path.isfile(explicit_path):
            return explicit_path
        raise FileNotFoundError(f"指定された ShogunPostCL が見つかりません: {explicit_path}")

    candidates = _enumerate_cl()
    if not candidates:
        raise FileNotFoundError(
            "ShogunPostCL.exe が見つかりません。--cl-path で明示指定してください。"
        )

    ver = sdk_version()
    if ver:
        parts = ver.split(".")
        try:
            key = (int(parts[0]), int(parts[1]))
            if key in candidates:
                return candidates[key]
            print(f"警告: SDK {ver} に一致する ShogunPostCL{key[0]}.{key[1]} が無いため"
                  "別バージョンを使用します（プロトコル不一致の可能性）。")
        except (ValueError, IndexError):
            pass

    latest = sorted(candidates.keys(), reverse=True)[0]
    return candidates[latest]


def discover_x2d_files(directory, recursive=False):
    """directory 内の *.x2d を列挙して絶対パスのリストで返す。"""
    if not os.path.isdir(directory):
        raise NotADirectoryError(f"フォルダが見つかりません: {directory}")

    if recursive:
        files = glob.glob(os.path.join(directory, "**", "*.x2d"), recursive=True)
    else:
        files = glob.glob(os.path.join(directory, "*.x2d"))

    files = [f for f in files if f.lower().endswith(".x2d")]
    return sorted(set(os.path.abspath(f) for f in files))


class BatchProgress:
    """バッチ全体のプログレスバーと、処理中ファイルのライブ経過表示。

    TTY 環境では 1 行を \\r で上書きしてバー・パーセント・スピナー・経過秒を
    アニメーション表示する。パイプ/リダイレクト等の非 TTY 環境では従来どおり
    ファイルごとに 1 行ずつ出力する（アニメーションしない）。
    """

    SPIN = "|/-\\"
    FILL = "█"   # █
    EMPTY = "░"  # ░

    # ShogunPostCL の stdout が 1 ファイルごとに出す粗いフェーズ進捗。
    # 例: "Default:Flow2GraphProcessor -> Processing Pass 2 of 3..."
    _PASS_RE = re.compile(r"Processing Pass (\d+) of (\d+)")

    def __init__(self, total, enabled=None, width=28):
        self.total = max(total, 0)
        self.width = width
        self.completed = 0
        self.enabled = (sys.stdout.isatty() and self.total > 0) if enabled is None else enabled
        self._stop = threading.Event()
        self._thread = None
        self._current = ""
        self._start = 0.0
        self._lock = threading.Lock()
        self._last_len = 0
        self._phase = 0          # 現在ファイルのフェーズ（0=未取得）
        self._phase_total = 0    # 総フェーズ数（Pass X of N の N）
        self._durations = []     # 完了ファイルの所要秒（ETA 用）

    @staticmethod
    def _short(name, maxlen=28):
        return name if len(name) <= maxlen else name[: maxlen - 1] + "…"

    @staticmethod
    def _fmt_eta(sec):
        m, s = divmod(int(sec + 0.5), 60)
        return f"{m}:{s:02d}"

    def _bar(self, frac):
        filled = int(round(frac * self.width))
        filled = max(0, min(self.width, filled))
        return self.FILL * filled + self.EMPTY * (self.width - filled)

    def on_log_line(self, raw):
        """ドレインスレッドから 1 行ずつ渡される。フェーズ進捗を拾う。"""
        m = self._PASS_RE.search(raw)
        if not m:
            return
        phase, phase_total = int(m.group(1)), int(m.group(2))
        with self._lock:
            self._phase = phase
            self._phase_total = phase_total
        # 非 TTY では上書き表示できないので、フェーズ遷移を 1 行だけ出す。
        if not self.enabled:
            print(f"        … Pass {phase}/{phase_total}", flush=True)

    def _subfrac(self):
        """現在ファイル内の進み具合（0.0〜1.0）。フェーズ未取得なら 0。"""
        if self._phase_total > 0:
            return min(self._phase / self._phase_total, 1.0)
        return 0.0

    def _eta_text(self):
        if not self._durations:
            return ""
        avg = sum(self._durations) / len(self._durations)
        remaining = self.total - self.completed  # 現在ファイルを含む残り件数
        est = avg * remaining - (time.time() - self._start)
        est = max(est, 0.0)
        return f"  残り~{self._fmt_eta(est)}"

    def _render(self):
        sub = self._subfrac()
        frac = (self.completed + sub) / self.total if self.total else 1.0
        pct = int(frac * 100)
        spin = self.SPIN[int(time.time() * 8) % len(self.SPIN)]
        elapsed = time.time() - self._start
        phase = f"Pass {self._phase}/{self._phase_total} " if self._phase_total else ""
        line = (f"\r[{self._bar(frac)}] {pct:3d}%  ({self.completed}/{self.total})  "
                f"{spin} {self._short(self._current)}  {phase}{elapsed:5.1f}s{self._eta_text()}")
        pad = max(0, self._last_len - len(line))
        sys.stdout.write(line + " " * pad)
        sys.stdout.flush()
        self._last_len = len(line)

    def _clear_line(self):
        if self.enabled and self._last_len:
            sys.stdout.write("\r" + " " * self._last_len + "\r")
            sys.stdout.flush()
            self._last_len = 0

    def _animate(self):
        while not self._stop.wait(0.1):
            with self._lock:
                self._render()

    def start_file(self, name):
        with self._lock:
            self._current = name
            self._start = time.time()
            self._phase = 0
            self._phase_total = 0
        if self.enabled:
            self._stop.clear()
            with self._lock:
                self._render()
            self._thread = threading.Thread(target=self._animate, daemon=True)
            self._thread.start()
        else:
            print(f"[{self.completed + 1}/{self.total}] {name} を処理中...", flush=True)

    def finish_file(self, line, dt=None):
        if self.enabled and self._thread:
            self._stop.set()
            self._thread.join()
            self._thread = None
        self.completed += 1
        if dt is not None:
            self._durations.append(dt)
        self._clear_line()
        print(line, flush=True)

    def stop(self):
        """アニメーションを止めて行をクリアする（中断時用）。"""
        if self._thread:
            self._stop.set()
            self._thread.join()
            self._thread = None
        self._clear_line()

    def close(self):
        if self.enabled:
            frac = self.completed / self.total if self.total else 1.0
            pct = int(frac * 100)
            self._clear_line()
            print(f"[{self._bar(frac)}] {pct:3d}%  ({self.completed}/{self.total}) 完了",
                  flush=True)


def _drain_pipe(pipe, log_fh, on_line=None):
    """ShogunPostCL の出力をログファイルへ書き出すスレッド本体。

    on_line が渡された場合、各行をそのコールバックにも渡してフェーズ進捗を
    抽出させる（パース失敗がドレインを止めないよう例外は握りつぶす）。
    """
    for raw in iter(pipe.readline, ""):
        log_fh.write(raw)
        log_fh.flush()
        if on_line is not None:
            try:
                on_line(raw)
            except Exception:
                pass
    pipe.close()


def launch_cl(cl_path, log_path, on_line=None):
    """ShogunPostCL をヘッドレス起動し、(proc, log_fh, reader_thread) を返す。

    on_line を渡すと、ShogunPostCL の各出力行がそのコールバックにも渡される
    （プログレス表示がフェーズ進捗を拾うのに使う）。
    """
    workdir = os.path.dirname(cl_path)
    proc = subprocess.Popen(
        [cl_path], cwd=workdir,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="ignore",
    )
    log_fh = open(log_path, "w", encoding="utf-8")
    reader = threading.Thread(target=_drain_pipe, args=(proc.stdout, log_fh, on_line), daemon=True)
    reader.start()
    return proc, log_fh, reader


def connect_with_retry(ViconShogunPost, address, port, timeout=90.0):
    """ShogunPostCL の起動完了まで接続をリトライし、接続済みクライアントを返す。"""
    t0 = time.time()
    last_err = None
    while time.time() - t0 < timeout:
        try:
            v = ViconShogunPost.ViconShogunPost(address, port)
            return v, time.time() - t0
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise TimeoutError(f"ShogunPostCL へ {timeout:.0f} 秒以内に接続できませんでした: {last_err}")


def shutdown_cl(proc):
    """ShogunPostCL を終了する。"""
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.write("exit\n")
            proc.stdin.flush()
            proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


_PROC_LEVEL = {"reconstruct": "Reconstruct", "label": "Label", "solve": "Solve"}

_UNSUPPORTED_FORMATS = {"hdf", "vdf"}


def process_one(v, Offline, x2d_path, subjects, level, out_format):
    """X2D を 1 つ処理して指定形式でエクスポートする。(成功bool, メッセージ) を返す。

    処理は Offline.QuickPost（x2d オフライン処理専用の一括メソッド）を使う。
    個別の Reconstruct→AutoLabel→Solve を順次呼ぶと ShogunPostCL がクラッシュ
    するため、QuickPost に一本化している。
    """
    if out_format in _UNSUPPORTED_FORMATS:
        raise RuntimeError(
            f"{out_format} はヘッドレス保存に未対応です（SaveFile クラッシュ/エクスポータ無し）。"
            "fbx / c3d / trc を使用してください。"
        )

    out_path = os.path.splitext(x2d_path)[0] + "." + out_format
    steps = []

    v.NewScene()
    v.ImportFile(x2d_path, "selCreateNew")
    steps.append("import")

    for vsk in subjects:
        v.ImportFile(vsk, "selCreateNew")
    if subjects:
        steps.append("subjects")

    proc_level = _PROC_LEVEL[level]
    off = Offline()
    off.QuickPost(proc_level, Offline.PLAY_RANGE)
    steps.append("quickpost:" + proc_level)

    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except OSError:
            pass
    v.ExportFile(out_path, out_format)
    if not (os.path.exists(out_path) and os.path.getsize(out_path) > 0):
        raise RuntimeError(
            f"エクスポート失敗（{out_format} 非対応 or エクスポータ未搭載の可能性）: {out_path}"
        )
    steps.append("export:" + out_format)

    return True, f"{os.path.basename(out_path)} ({'+'.join(steps)})"


# ─── ワーカープロセス ──────────────────────────────────────────────
# QuickPost（SDK の C 関数）は実行中ずっと Python の GIL を保持するため、
# 同一プロセス内ではプログレス描画スレッドが動けない。そこで SDK 処理は
# この別プロセス（ワーカー）で実行し、親プロセスは ShogunPostCL の stdout と
# 下記のイベント行だけを読んでバーを描画する（親は SDK を呼ばないので GIL が
# 常に空き、スピナー/バーが滑らかに動く）。
_EVENT_PREFIX = "@@SPB@@"


def _emit(evt):
    """ワーカー→親へ 1 イベントを JSON 行で送る。"""
    sys.stdout.write(_EVENT_PREFIX + json.dumps(evt, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def worker_main(cfg_path):
    """別プロセスとして SDK に接続し、各 X2D を処理してイベントを emit する。"""
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    files = cfg["files"]
    subjects = cfg["subjects"]
    level = cfg["level"]
    out_format = cfg["out_format"]
    address = cfg["address"]
    port = cfg["port"]
    connect_timeout = cfg["connect_timeout"]

    try:
        ViconShogunPost, Offline = import_sdk()
    except SystemExit:
        # import_sdk は詳細な導入手順を stdout に出す（親がログへ退避）。
        _emit({"e": "fatal", "err": "ViconShogunPostSDK を読み込めません（ログ参照）"})
        return 1

    try:
        v, took = connect_with_retry(ViconShogunPost, address, port, connect_timeout)
    except TimeoutError as e:
        _emit({"e": "connect_failed", "err": str(e)})
        return 1
    _emit({"e": "connected", "took": took})

    for i, x2d in enumerate(files, 1):
        _emit({"e": "start", "i": i, "name": os.path.basename(x2d)})
        t0 = time.time()
        try:
            ok, msg = process_one(v, Offline, x2d, subjects, level, out_format)
            _emit({"e": "ok", "path": x2d, "msg": msg, "dt": time.time() - t0})
        except Exception as e:
            _emit({"e": "ng", "path": x2d, "name": os.path.basename(x2d),
                   "err": str(e), "dt": time.time() - t0})

    try:
        v.Disconnect() if hasattr(v, "Disconnect") else None
    except Exception:
        pass
    _emit({"e": "done"})
    return 0


def _spawn_worker(cfg):
    """SDK 処理ワーカーを別プロセスとして起動し、Popen を返す。"""
    cfg_fd, cfg_path = tempfile.mkstemp(suffix=".json", prefix="spb_cfg_")
    with os.fdopen(cfg_fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    worker = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--_worker", cfg_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="ignore",
    )
    return worker, cfg_path


def run_batch(x2d_files, subjects, level, out_format, cl_path, address, port,
              connect_timeout, log_path, show_progress=None):
    print(f"ShogunPostCL : {cl_path}")
    print(f"SDK バージョン: {sdk_version()}")
    print(f"接続先        : {address}:{port}")
    print(f"処理レベル    : {level} (QuickPost:{_PROC_LEVEL[level]})")
    print(f"出力形式      : .{out_format}")
    print(f"ログ          : {log_path}")
    print("-" * 64)

    # プログレス表示は親プロセスで描画する。親は SDK を呼ばない（＝GIL が空く）
    # ので、ShogunPostCL の出力行を on_log_line へ流してフェーズ進捗（Pass N/3）
    # をライブで拾い、バー/スピナー/ETA を滑らかに更新できる。
    progress = BatchProgress(len(x2d_files), enabled=show_progress)

    print("ShogunPostCL を起動しています...", flush=True)
    proc, log_fh, reader = launch_cl(cl_path, log_path, on_line=progress.on_log_line)

    cfg = {
        "files": x2d_files, "subjects": subjects, "level": level,
        "out_format": out_format, "address": address, "port": port,
        "connect_timeout": connect_timeout,
    }

    results = []
    worker = None
    cfg_path = None
    try:
        print("接続待ち（別プロセスで SDK 起動・接続）...", flush=True)
        worker, cfg_path = _spawn_worker(cfg)

        # ワーカーの stdout を読み、イベント行でバーを駆動する。
        # イベント以外（SDK/import の出力）はログへ退避。
        for raw in worker.stdout:
            if not raw.startswith(_EVENT_PREFIX):
                log_fh.write(raw)
                log_fh.flush()
                continue
            try:
                evt = json.loads(raw[len(_EVENT_PREFIX):])
            except ValueError:
                continue
            e = evt.get("e")
            if e == "connected":
                print(f"接続しました（{evt.get('took', 0.0):.1f} 秒）", flush=True)
                print("-" * 64, flush=True)
            elif e in ("connect_failed", "fatal"):
                print(f"エラー: {evt.get('err')}", flush=True)
            elif e == "start":
                progress.start_file(evt.get("name", ""))
            elif e == "ok":
                dt = evt.get("dt")
                progress.finish_file(f"    OK  {evt.get('msg')}  ({dt:.1f}s)", dt)
                results.append((evt.get("path"), True, evt.get("msg")))
            elif e == "ng":
                dt = evt.get("dt")
                progress.finish_file(
                    f"    NG  {evt.get('name', '')}: {evt.get('err', '')}  ({dt:.1f}s)", dt)
                results.append((evt.get("path"), False, evt.get("err", "")))
            # "done" は特に何もしない（この後 stdout が閉じてループ終了）
        progress.close()
    except KeyboardInterrupt:
        progress.stop()
        print("\n中断要求を受け取りました。終了します...", flush=True)
        if worker is not None:
            worker.terminate()
    finally:
        if worker is not None and worker.poll() is None:
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
        shutdown_cl(proc)
        reader.join(timeout=3)
        log_fh.close()
        if cfg_path:
            try:
                os.remove(cfg_path)
            except OSError:
                pass

    return results


def main():
    parser = argparse.ArgumentParser(
        description="X2D を ShogunPostCL でバッチ再計算（QuickPost）して fbx 等にエクスポートする。"
    )
    parser.add_argument("directory", nargs="?", help="X2D ファイルが入っているフォルダ")
    parser.add_argument("--recursive", "-r", action="store_true",
                        help="サブフォルダも再帰的に検索する")
    parser.add_argument("--level", choices=["reconstruct", "label", "solve"], default="solve",
                        help="処理の深さ: reconstruct / label / solve(既定)。QuickPost の procLevel")
    parser.add_argument("--subjects", nargs="*", default=[],
                        help="読み込む VSK ファイル（複数可、またはフォルダ）。省略時は読み込まない")
    parser.add_argument("--format", dest="out_format",
                        choices=["fbx", "c3d", "trc"], default="fbx",
                        help="エクスポート形式（既定 fbx）。※hdf/vdf はヘッドレス非対応")
    parser.add_argument("--cl-path", default=None,
                        help="ShogunPostCL.exe のパス（省略時は SDK バージョンに合わせて自動検出）")
    parser.add_argument("--address", default="localhost", help="接続先アドレス（既定 localhost）")
    parser.add_argument("--port", type=int, default=803, help="SDK 制御ポート（既定 803）")
    parser.add_argument("--connect-timeout", type=float, default=90.0,
                        help="接続リトライのタイムアウト秒数（既定 90）")
    parser.add_argument("--limit", type=int, default=0,
                        help="先頭 N 件だけ処理する（0=全件、テスト用）")
    parser.add_argument("--dry-run", action="store_true",
                        help="対象ファイルの列挙だけ行い、実行しない")
    parser.add_argument("--no-progress", action="store_true",
                        help="プログレスバー表示を無効化し、ファイルごとに1行ずつ出力する")
    args = parser.parse_args()

    directory = args.directory
    if not directory:
        directory = input("X2D フォルダのパスを入力してください: ").strip().strip('"')
    directory = os.path.abspath(directory)

    subjects = []
    for s in args.subjects:
        if os.path.isdir(s):
            subjects.extend(sorted(glob.glob(os.path.join(s, "*.vsk"))))
        elif os.path.isfile(s):
            subjects.append(os.path.abspath(s))
        else:
            print(f"警告: サブジェクトが見つかりません（無視します）: {s}")
    subjects = [os.path.abspath(s) for s in subjects]

    try:
        x2d_files = discover_x2d_files(directory, recursive=args.recursive)
    except (NotADirectoryError, FileNotFoundError) as e:
        print(f"エラー: {e}")
        return 1
    if not x2d_files:
        print(f"X2D ファイルが見つかりませんでした: {directory}"
              + ("（--recursive で再帰検索できます）" if not args.recursive else ""))
        return 1

    if args.limit and args.limit > 0:
        x2d_files = x2d_files[:args.limit]

    print(f"対象フォルダ : {directory}")
    print(f"処理レベル   : {args.level}（QuickPost）")
    print(f"出力形式     : .{args.out_format}（エクスポート）")
    print(f"X2D ファイル : {len(x2d_files)} 件")
    for f in x2d_files:
        print("   - " + os.path.basename(f))
    if subjects:
        print(f"サブジェクト : {len(subjects)} 件")
        for s in subjects:
            print("   - " + os.path.basename(s))

    if args.dry_run:
        print("--dry-run: 実行はしていません。")
        return 0

    try:
        cl_path = find_shogunpost_cl(args.cl_path)
    except FileNotFoundError as e:
        print(f"エラー: {e}")
        return 1

    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"shogun_batch_{stamp}.log")

    print("-" * 64)
    start = time.time()
    show_progress = False if args.no_progress else None
    results = run_batch(x2d_files, subjects, args.level, args.out_format, cl_path,
                        args.address, args.port, args.connect_timeout, log_path,
                        show_progress=show_progress)
    elapsed = time.time() - start

    print("-" * 64)
    ok = sum(1 for _, s, _ in results if s)
    ng = len(results) - ok
    print(f"完了: {ok}/{len(x2d_files)} 件保存  失敗: {ng} 件")
    print(f"所要時間: {elapsed/60:.1f} 分")
    print(f"ログ: {log_path}")
    if ng:
        print("失敗したファイル:")
        for path, s, msg in results:
            if not s:
                print(f"   - {os.path.basename(path)}: {msg}")
        return 2
    return 0


if __name__ == "__main__":
    # 別プロセス（ワーカー）として起動された場合は SDK 処理だけを行う。
    if len(sys.argv) >= 3 and sys.argv[1] == "--_worker":
        sys.exit(worker_main(sys.argv[2]))
    sys.exit(main())
