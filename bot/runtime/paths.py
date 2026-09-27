"""프로젝트 루트와 런타임 디렉터리 경로 헬퍼."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_app_directory() -> Path:
    """데이터, 로그, `.env`가 위치하는 프로젝트 루트를 반환한다."""

    return PROJECT_ROOT


def ensure_runtime_directories(app_directory: Path) -> tuple[Path, Path]:
    """data와 logs 디렉터리를 만들고 경로를 반환한다."""

    data_directory = app_directory / "data"
    logs_directory = app_directory / "logs"
    data_directory.mkdir(parents=True, exist_ok=True)
    logs_directory.mkdir(parents=True, exist_ok=True)
    return data_directory, logs_directory
