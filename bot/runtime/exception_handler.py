"""User-facing startup error helpers."""

from pathlib import Path


def wait_before_exit() -> None:
    """Wait for Enter so double-clicked console errors remain visible."""

    try:
        input("\n프로그램을 종료하려면 Enter 키를 누르세요.")
    except (EOFError, KeyboardInterrupt):
        pass


def print_startup_error(error: BaseException, log_path: Path) -> None:
    """Print a concise error message and point the user to the log file."""

    print("\nDiscord 출석 봇을 시작하지 못했습니다.")
    print(f"오류: {error}")
    print(f"자세한 내용은 로그 파일을 확인하세요: {log_path}")
