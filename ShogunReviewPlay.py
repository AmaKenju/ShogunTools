import sys

from vicon_core_api import Client
from shogun_live_api import CaptureServices, PlaybackServices


def resolve_capture_name(capture, playback):
    result, _id, name = capture.latest_capture_name()
    if result and name:
        return name

    result, captures = playback.capture_list()
    if not result:
        raise RuntimeError(f"キャプチャ一覧を取得できませんでした: {result}")
    if not captures:
        _, folder = playback.review_folder()
        raise RuntimeError(f"レビュー可能なキャプチャがありません: {folder}")

    return max(captures, key=lambda m: m.epoch_time).capture_name


def play_review(ip_address="localhost", capture_name=None):
    print(f"{ip_address} に接続しています...")
    client = Client(ip_address)
    capture = CaptureServices(client)
    playback = PlaybackServices(client)
    print(f"{ip_address} に接続")

    if capture_name is None:
        capture_name = resolve_capture_name(capture, playback)

    result = playback.enter_capture_review(capture_name)
    if not result:
        playback.exit_review()
        result = playback.enter_capture_review(capture_name)
        if not result:
            raise RuntimeError(f"レビューを開始できませんでした: {result}")

    result = playback.play()
    if not result:
        raise RuntimeError(f"再生を開始できませんでした: {result}")

    print(f"再生中: {capture_name}")


def main():
    ip_address = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    capture_name = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        play_review(ip_address, capture_name)
    except Exception as e:
        print(f"エラー: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
