"""
post_pipeline.py
================
「撮りながら別マシンで Post Process」を実現するパイプライン部品。

役割:
  - PostProcessWorker : ShogunPostCL を 1 つ常駐させ、キューに入ってきた
                        X2D を順次 Post Process（Reconstruct→AutoLabel→Solve）
                        して保存する常駐ワーカー（バックグラウンドスレッド）。
  - CaptureRelay      : Shogun Live のキャプチャ停止→完了を待ち、書き出された
                        ファイルを共有(UNC)経由で PC-B のローカルへコピーし、
                        その X2D をワーカーのキューへ投入する。

前提（今回の運用）:
  - 撮影データは撮影マシン(PC-A)のローカルに保存され、PC-A 側が共有されている。
    PC-A のローカルパス（例 D:\\Data\\take.x2d）を UNC（例 \\\\PC-A\\Data\\take.x2d）
    に変換して PC-B からコピーする。変換規則は PipelineConfig.path_map で指定。
  - Post Process は ShogunPostBatch.py の検証済みロジック（SDK 駆動）を再利用。

このモジュールは tkinter に依存しないので単体でも使える。ShogunControl_v7.py
からは PostProcessWorker と CaptureRelay を生成して stop 時に呼び出す。
"""

import os
import queue
import shutil
import threading
import time
import traceback

import ShogunPostBatch as spb


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
class PipelineConfig:
    def __init__(
        self,
        capture_host,                 # PC-A のホスト名 or IP（UNC 変換のフォールバックに使用）
        local_dest_root,              # PC-B 側のコピー先ルートフォルダ
        path_map=None,                # [(local_prefix, unc_prefix), ...] 変換規則（前方一致）
        use_admin_share_fallback=True,  # 規則に当たらない時 D:\x -> \\host\D$\x を試す
        level="solve",                # reconstruct / label / solve（QuickPost の procLevel）
        out_format="fbx",             # fbx / c3d / trc（hdf/vdf はヘッドレス非対応）
        subjects=None,                # 読み込む VSK（任意）
        cl_path=None,                 # ShogunPostCL.exe（省略時自動）
        address="localhost",          # ShogunPostCL の SDK 接続先
        port=803,                     # SDK 制御ポート
        connect_timeout=90.0,
        copy_extra_patterns=None,     # x2d と同じフォルダから追加コピーする拡張子
        log_dir=None,
    ):
        self.capture_host = capture_host
        self.local_dest_root = local_dest_root
        self.path_map = path_map or []
        self.use_admin_share_fallback = use_admin_share_fallback
        self.level = level
        self.out_format = out_format
        self.subjects = subjects or []
        self.cl_path = cl_path
        self.address = address
        self.port = port
        self.connect_timeout = connect_timeout
        # 再構成にはキャリブレーション等が必要なため、テイクと同じフォルダにある
        # 校正・システム系ファイルも併せてコピーしておく
        self.copy_extra_patterns = (
            copy_extra_patterns
            if copy_extra_patterns is not None
            else [".xcp", ".cp", ".mcp", ".cal", ".system", ".vsk", ".enf"]
        )
        self.log_dir = log_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "logs"
        )



# ---------------------------------------------------------------------------
# パス変換（PC-A ローカル -> UNC）
# ---------------------------------------------------------------------------
def translate_to_unc(local_path, config):
    """撮影マシンのローカルパスを PC-B からアクセスできる UNC パスへ変換する。"""
    if not local_path:
        return local_path
    p = local_path.replace("/", "\\")

    # 既に UNC ならそのまま
    if p.startswith("\\\\"):
        return p

    # 明示マッピング（前方一致・大文字小文字無視）
    for local_prefix, unc_prefix in config.path_map:
        lp = local_prefix.replace("/", "\\")
        if p.lower().startswith(lp.lower()):
            rest = p[len(lp):].lstrip("\\")
            return unc_prefix.rstrip("\\") + "\\" + rest

    # ドライブレター -> 管理共有 (\\host\D$\...)
    if config.use_admin_share_fallback and len(p) >= 2 and p[1] == ":":
        drive = p[0]
        rest = p[2:].lstrip("\\")
        return f"\\\\{config.capture_host}\\{drive}$\\{rest}"

    return p  # 変換できない場合はそのまま（コピー時に失敗→ログ）


# ---------------------------------------------------------------------------
# キャプチャファイルのコピー
# ---------------------------------------------------------------------------
def copy_capture_files(file_paths, config, log=print):
    """キャプチャが書き出したファイル群を PC-B のローカルへコピーする。

    Returns:
      (dest_folder, [ローカルにコピーされた x2d パス, ...])
    """
    if not file_paths:
        return None, []

    x2d_sources = [p for p in file_paths if p.lower().endswith(".x2d")]
    if not x2d_sources:
        log("コピー: x2d が見つかりません: %s" % file_paths)
        return None, []

    # テイク名 = 最初の x2d のベース名（拡張子無し）
    take_name = os.path.splitext(os.path.basename(x2d_sources[0]))[0]
    dest_folder = os.path.join(config.local_dest_root, take_name)
    os.makedirs(dest_folder, exist_ok=True)

    # 1) キャプチャが報告した全ファイルをコピー
    to_copy = list(file_paths)

    # 2) x2d と同じフォルダにある校正/システム系ファイルも追加（API が返さない場合の保険）
    if config.copy_extra_patterns:
        src_dir_local = os.path.dirname(x2d_sources[0])
        src_dir_unc = translate_to_unc(src_dir_local, config)
        try:
            for name in os.listdir(src_dir_unc):
                ext = os.path.splitext(name)[1].lower()
                if ext in config.copy_extra_patterns:
                    full_local = os.path.join(src_dir_local, name)
                    if full_local not in to_copy:
                        to_copy.append(full_local)
        except OSError as e:
            log(f"コピー: 追加校正ファイルの列挙に失敗（無視）: {e}")

    copied_x2d = []
    for src_local in to_copy:
        src = translate_to_unc(src_local, config)
        dst = os.path.join(dest_folder, os.path.basename(src_local))
        try:
            shutil.copy2(src, dst)
            if dst.lower().endswith(".x2d"):
                copied_x2d.append(dst)
        except OSError as e:
            log(f"コピー失敗: {src} -> {dst}: {e}")

    log(f"コピー完了: {len(copied_x2d)} 個の x2d を {dest_folder} へ")
    return dest_folder, copied_x2d


# ---------------------------------------------------------------------------
# 常駐 Post Process ワーカー
# ---------------------------------------------------------------------------
class PostProcessWorker:
    """ShogunPostCL を 1 つ常駐させ、キューの x2d を順次処理する。"""

    _SENTINEL = object()

    def __init__(self, config, log=print):
        self.config = config
        self.log = log
        self._queue = queue.Queue()
        self._proc = None
        self._log_fh = None
        self._reader = None
        self._v = None
        self._Offline = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # --- public ---
    def enqueue(self, x2d_path):
        """処理対象の x2d をキューへ追加する（複数可・スレッドセーフ）。"""
        if isinstance(x2d_path, (list, tuple)):
            for p in x2d_path:
                self._queue.put(p)
        else:
            self._queue.put(x2d_path)

    def pending(self):
        return self._queue.qsize()

    def shutdown(self, wait=True):
        """ワーカーを停止し、ShogunPostCL を終了する。"""
        self._queue.put(self._SENTINEL)
        self._stop.set()
        if wait:
            self._thread.join(timeout=30)

    # --- internal ---
    def _connect(self):
        ViconShogunPost, Offline = spb.import_sdk()
        cl_path = spb.find_shogunpost_cl(self.config.cl_path)
        os.makedirs(self.config.log_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(self.config.log_dir, f"post_worker_{stamp}.log")
        self.log(f"ワーカー: ShogunPostCL を起動 ({cl_path})")
        self._proc, self._log_fh, self._reader = spb.launch_cl(cl_path, log_path)
        self._v, took = spb.connect_with_retry(
            ViconShogunPost, self.config.address, self.config.port,
            self.config.connect_timeout,
        )
        self._Offline = Offline
        self.log(f"ワーカー: 接続しました（{took:.1f}s）。投入待機中")
        self._ready.set()

    def _run(self):
        try:
            self._connect()
        except Exception as e:
            self.log(f"ワーカー起動失敗: {e}")
            traceback.print_exc()
            return

        while True:
            item = self._queue.get()
            if item is self._SENTINEL:
                break
            x2d = item
            name = os.path.basename(x2d)
            self.log(f"ワーカー: 処理開始 {name}（残り {self._queue.qsize()} 件）")
            t0 = time.time()
            try:
                ok, msg = spb.process_one(
                    self._v, self._Offline, x2d, self.config.subjects,
                    self.config.level, self.config.out_format,
                )
                self.log(f"ワーカー: 完了 {msg}（{time.time()-t0:.1f}s）")
            except Exception as e:
                self.log(f"ワーカー: 失敗 {name}: {e}（{time.time()-t0:.1f}s）")

        # 後始末
        try:
            spb.shutdown_cl(self._proc)
        finally:
            if self._reader:
                self._reader.join(timeout=3)
            if self._log_fh:
                self._log_fh.close()
        self.log("ワーカー: 停止しました")


# ---------------------------------------------------------------------------
# キャプチャ完了 → コピー → 投入
# ---------------------------------------------------------------------------
class CaptureRelay:
    """Shogun Live のキャプチャ停止を受け、完了待ち→コピー→ワーカー投入を行う。"""

    def __init__(self, shogun_capture, worker, config, log=print):
        self.cap = shogun_capture       # CaptureServices インスタンス
        self.worker = worker            # PostProcessWorker
        self.config = config
        self.log = log
        # CaptureServices.EState を取得
        self._EState = type(shogun_capture).EState

    def on_capture_stopped(self, capture_id=None):
        """停止直後に呼ぶ。完了待ち以降をバックグラウンドで実行し、すぐ戻る。"""
        t = threading.Thread(
            target=self._wait_copy_enqueue, args=(capture_id,), daemon=True
        )
        t.start()
        return t

    def _get_state(self):
        """(id, state) を返す。取得失敗時は (None, None)。"""
        try:
            res = self.cap.latest_capture_state()  # (Result, id, state)
            return res[1], res[2]
        except Exception as e:
            self.log(f"リレー: 状態取得失敗: {e}")
            return None, None

    def _wait_for_complete(self, capture_id, timeout=600.0, poll=1.0):
        """指定キャプチャ（or 最新）が ECompleted になるまで待つ。"""
        completed = self._EState.ECompleted
        canceled = getattr(self._EState, "ECanceled", None)
        t0 = time.time()
        while time.time() - t0 < timeout:
            cid, state = self._get_state()
            if state is None:
                time.sleep(poll)
                continue
            # 値・enum どちらでも比較できるように
            sval = getattr(state, "value", state)
            if sval == getattr(completed, "value", completed):
                return True
            if canceled is not None and sval == getattr(canceled, "value", canceled):
                self.log("リレー: キャプチャがキャンセルされました")
                return False
            time.sleep(poll)
        self.log("リレー: 完了待ちタイムアウト")
        return False

    def _wait_copy_enqueue(self, capture_id):
        try:
            self.log("リレー: キャプチャ完了を待っています...")
            if not self._wait_for_complete(capture_id):
                return
            # 書き出されたファイルパスを取得
            res = self.cap.latest_capture_file_paths()  # (Result, id, [paths])
            file_paths = list(res[2]) if res and len(res) >= 3 else []
            if not file_paths:
                self.log("リレー: 書き出しファイルが取得できませんでした")
                return
            self.log(f"リレー: {len(file_paths)} 個のファイルをコピーします")
            dest_folder, x2ds = copy_capture_files(file_paths, self.config, self.log)
            if not x2ds:
                self.log("リレー: コピーされた x2d がありません")
                return
            self.worker.enqueue(x2ds)
            self.log(f"リレー: {len(x2ds)} 個の x2d をワーカーへ投入しました")
        except Exception as e:
            self.log(f"リレー: 例外: {e}")
            traceback.print_exc()
