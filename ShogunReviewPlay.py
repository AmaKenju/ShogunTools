import sys

from vicon_core_api import Client
from shogun_live_api import CaptureServices, PlaybackServices


def play_review(ip_address="localhost", capture_name=None):
    print(f"{ip_address} に接続しています...")
    client = Client(ip_address)
    capture = CaptureServices(client)
    playback = PlaybackServices(client)
    print(f"{ip_address} に接続")

    if capture_name is None:
        result = capture.latest_capture_name()
        if not result or len(result) < 3:
            raise RuntimeError("最新のキャプチャ名を取得できませんでした")
        capture_name = result[2]

    print(f"レビューを開始: {capture_name}")
    playback.enter_capture_review(capture_name)
    playback.play()
    print("再生中")


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
