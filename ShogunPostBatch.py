"""
ShogunPostBatch.py
==================
指定フォルダ内の X2D ファイルを ShogunPostCL.exe（ヘッドレス）で
バッチ再計算（Post Process: Reconstruct → AutoLabel → Solve）し、
X2D と同じ場所に同名で保存するツール（既定 .hdf、--format で .vdf 等も可）。

仕組み:
  1. インストール済みの ViconShogunPostSDK のバージョンに合う
     ShogunPostCL.exe をヘッドレス起動する。
  2. SDK（ViconShogunPost）でその ShogunPostCL に制御接続する
     （起動完了まで接続をリトライ）。
  3. 各 X2D について以下を実行:
       NewScene → ImportFile(x2d) →〔任意で VSK Import〕→
       Offline.Reconstruct →〔AutoLabel〕→〔Solve〕→ SaveFile(.hdf)
  4. 切断して ShogunPostCL を終了する。

前提・注意:
  - ViconShogunPostSDK が pip 導入済みであること
    （`import ViconShogunPost` が通ること）。Shogun Post 付属の
    SDK\\Win64\\install_vicon_shogun_post_sdk.bat で導入できる。
  - AutoLabel / Solve はシーンにサブジェクトが必要。X2D/HDF に
    サブジェクトが含まれない場合は --subjects で VSK を指定するか、
    --level reconstruct で再構成のみ実行する。
  - SDK 制御ポート（既定 803）を使うため、同一マシンで対話用の
    Shogun Post を同時起動していると接続先が競合する可能性がある。
  - ShogunPostCL は実行中ライセンスを 1 つ消費する。

使用例:
  python ShogunPostBatch.py "D:/Capture/Day1/Session1"
  python ShogunPostBatch.py "D:/Capture" --recursive
  python ShogunPostBatch.py "D:/Capture" --level reconstruct
  python ShogunPostBatch.py "D:/Capture" --subjects "D:/Subjects/actor.vsk"
"""

import argparse
import glob
import os
import subprocess
import sys
import threading
import time

# Windows の cp932 コンソールでも日本語を文字化けさせないよう UTF-8 出力にする
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

VICON_DIR = r"C:\Program Files\Vicon"


# ---------------------------------------------------------------------------
# SDK のインポート
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# ShogunPostCL.exe の検出（SDK バージョンに合わせる）
# ---------------------------------------------------------------------------
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

    # SDK バージョンに一致する CL を優先
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


# ---------------------------------------------------------------------------
# X2D ファイルの列挙
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# ShogunPostCL の起動 / 接続 / 終了
# ---------------------------------------------------------------------------
def _drain_pipe(pipe, log_fh):
    """ShogunPostCL の出力をログファイルへ書き出すスレッド本体。"""
    for raw in iter(pipe.readline, ""):
        log_fh.write(raw)
        log_fh.flush()
    pipe.close()


def launch_cl(cl_path, log_path):
    """ShogunPostCL をヘッドレス起動し、(proc, log_fh, reader_thread) を返す。"""
    workdir = os.path.dirname(cl_path)
    proc = subprocess.Popen(
        [cl_path], cwd=workdir,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="ignore",
    )
    log_fh = open(log_path, "w", encoding="utf-8")
    reader = threading.Thread(target=_drain_pipe, args=(proc.stdout, log_fh), daemon=True)
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
        except Exception as e:  # 起動途中は接続失敗するのでリトライ
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


# ---------------------------------------------------------------------------
# 1 ファイルの処理
# ---------------------------------------------------------------------------
def process_one(v, Offline, x2d_path, subjects, do_label, do_solve, out_format):
    """X2D を 1 つ処理して指定形式で保存する。(成功bool, メッセージ) を返す。"""
    out_path = os.path.splitext(x2d_path)[0] + "." + out_format
    steps = []

    v.NewScene()
    v.ImportFile(x2d_path, "selCreateNew")
    steps.append("import")

    for vsk in subjects:
        v.ImportFile(vsk, "selCreateNew")
    if subjects:
        steps.append("subjects")

    off = Offline()
    off.Reconstruct(Offline.PLAY_RANGE)
    steps.append("reconstruct")

    if do_label:
        off.AutoLabel(Offline.SUBJECTS_ALL, Offline.PLAY_RANGE)
        steps.append("label")

    if do_solve:
        off.Solve(Offline.PLAY_RANGE)
        steps.append("solve")

    # hdf はネイティブ保存(SaveFile)、それ以外(vdf/c3d/bvh/fbx 等)はエクスポート(ExportFile)。
    # ※ SaveFile に vdf を渡すと応答停止するため、必ず ExportFile を使う。
    if out_format == "hdf":
        v.SaveFile(out_path)
    else:
        v.ExportFile(out_path, out_format)
    steps.append("save")

    return True, f"{os.path.basename(out_path)} ({'+'.join(steps)})"


# ---------------------------------------------------------------------------
# バッチ本体
# ---------------------------------------------------------------------------
def run_batch(x2d_files, subjects, do_label, do_solve, out_format, cl_path, address, port,
              connect_timeout, log_path):
    ViconShogunPost, Offline = import_sdk()

    print(f"ShogunPostCL : {cl_path}")
    print(f"SDK バージョン: {sdk_version()}")
    print(f"接続先        : {address}:{port}")
    print(f"保存形式      : .{out_format}")
    print(f"ログ          : {log_path}")
    print("-" * 64)

    print("ShogunPostCL を起動しています...", flush=True)
    proc, log_fh, reader = launch_cl(cl_path, log_path)

    results = []
    try:
        print("接続待ち（起動完了までリトライ）...", flush=True)
        try:
            v, took = connect_with_retry(ViconShogunPost, address, port, connect_timeout)
        except TimeoutError as e:
            print(f"エラー: {e}")
            return []
        print(f"接続しました（{took:.1f} 秒）", flush=True)
        print("-" * 64, flush=True)

        total = len(x2d_files)
        for i, x2d in enumerate(x2d_files, 1):
            name = os.path.basename(x2d)
            print(f"[{i}/{total}] {name} を処理中...", flush=True)
            t0 = time.time()
            try:
                ok, msg = process_one(v, Offline, x2d, subjects, do_label, do_solve, out_format)
                dt = time.time() - t0
                print(f"    OK  {msg}  ({dt:.1f}s)", flush=True)
                results.append((x2d, True, msg))
            except Exception as e:
                dt = time.time() - t0
                print(f"    NG  {name}: {e}  ({dt:.1f}s)", flush=True)
                results.append((x2d, False, str(e)))

        # 切断
        try:
            v.Disconnect() if hasattr(v, "Disconnect") else None
        except Exception:
            pass
    except KeyboardInterrupt:
        print("\n中断要求を受け取りました。ShogunPostCL を終了します...", flush=True)
    finally:
        shutdown_cl(proc)
        reader.join(timeout=3)
        log_fh.close()

    return results


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="X2D を ShogunPostCL でバッチ再計算（Post Process）して .hdf 保存する。"
    )
    parser.add_argument("directory", nargs="?", help="X2D ファイルが入っているフォルダ")
    parser.add_argument("--recursive", "-r", action="store_true",
                        help="サブフォルダも再帰的に検索する")
    parser.add_argument("--level", choices=["reconstruct", "label", "solve"], default="solve",
                        help="処理の深さ: reconstruct / label(=+AutoLabel) / solve(=+Solve, 既定)")
    parser.add_argument("--subjects", nargs="*", default=[],
                        help="読み込む VSK ファイル（複数可、またはフォルダ）。省略時は読み込まない")
    parser.add_argument("--format", dest="out_format",
                        choices=["hdf", "vdf", "c3d", "bvh", "fbx"], default="hdf",
                        help="保存形式（既定 hdf）。hdf はネイティブ保存、それ以外はエクスポート")
    parser.add_argument("--cl-path", default=None,
                        help="ShogunPostCL.exe のパス（省略時は SDK バージョンに合わせて自動検出）")
    parser.add_argument("--address", default="localhost", help="接続先アドレス（既定 localhost）")
    parser.add_argument("--port", type=int, default=803, help="SDK 制御ポート（既定 803）")
    parser.add_argument("--connect-timeout", type=float, default=90.0,
                        help="接続リトライのタイムアウト秒数（既定 90）")
    parser.add_argument("--dry-run", action="store_true",
                        help="対象ファイルの列挙だけ行い、実行しない")
    args = parser.parse_args()

    directory = args.directory
    if not directory:
        directory = input("X2D フォルダのパスを入力してください: ").strip().strip('"')
    directory = os.path.abspath(directory)

    do_label = args.level in ("label", "solve")
    do_solve = args.level == "solve"

    # サブジェクト VSK の解決
    subjects = []
    for s in args.subjects:
        if os.path.isdir(s):
            subjects.extend(sorted(glob.glob(os.path.join(s, "*.vsk"))))
        elif os.path.isfile(s):
            subjects.append(os.path.abspath(s))
        else:
            print(f"警告: サブジェクトが見つかりません（無視します）: {s}")
    subjects = [os.path.abspath(s) for s in subjects]

    # X2D 列挙
    try:
        x2d_files = discover_x2d_files(directory, recursive=args.recursive)
    except (NotADirectoryError, FileNotFoundError) as e:
        print(f"エラー: {e}")
        return 1
    if not x2d_files:
        print(f"X2D ファイルが見つかりませんでした: {directory}"
              + ("（--recursive で再帰検索できます）" if not args.recursive else ""))
        return 1

    print(f"対象フォルダ : {directory}")
    print(f"処理レベル   : {args.level}"
          + ("（label/solve は VSK 未指定。X2D/HDF にサブジェクトが無いと空振りの可能性）"
             if (do_label or do_solve) and not subjects else ""))
    print(f"保存形式     : .{args.out_format}"
          + ("（ネイティブ保存）" if args.out_format == "hdf" else "（エクスポート）"))
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

    # CL 検出
    try:
        cl_path = find_shogunpost_cl(args.cl_path)
    except FileNotFoundError as e:
        print(f"エラー: {e}")
        return 1

    # ログ
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"shogun_batch_{stamp}.log")

    print("-" * 64)
    start = time.time()
    results = run_batch(x2d_files, subjects, do_label, do_solve, args.out_format, cl_path,
                        args.address, args.port, args.connect_timeout, log_path)
    elapsed = time.time() - start

    # 集計
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
    sys.exit(main())
