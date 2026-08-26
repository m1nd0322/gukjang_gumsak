#!/usr/bin/env python3
"""임계값을 넘은 로그 파일을 세대 교체 방식으로 회전한다.

launchd가 표준 출력을 계속 덧붙이는 구조라 별도 설정 없이는 로그가
무한히 커진다. 서버 시작 직후처럼 쓰고 있던 프로세스가 확실히 끝난
시점에 ``rotate_if_large``를 호출하면 안전하게 회전할 수 있다.

실행: python scripts/rotate_logs.py [--max-mb 5] [--generations 3]
"""

import argparse
import os
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = REPO_DIR / ".omx" / "logs"
DEFAULT_LOG_NAMES = ("web.log", "daily-refresh.log", "db-backup.log")
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_GENERATIONS = 3


def rotate_if_large(
    path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    generations: int = DEFAULT_GENERATIONS,
):
    """파일이 max_bytes를 넘으면 오래된 세대부터 밀어내며 회전한다.

    web.log → web.log.1 → web.log.2 순으로 밀고 마지막 세대는 삭제한다.
    회전이 일어나면 원래 경로를, 기준보다 작거나 없으면 None을 반환한다.
    """
    path = Path(path)
    generations = max(1, int(generations))
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    if size < max(1, int(max_bytes)):
        return None

    oldest = Path(f"{path}.{generations}")
    if oldest.exists():
        os.unlink(oldest)
    for index in range(generations - 1, 0, -1):
        source = Path(f"{path}.{index}")
        if source.exists():
            os.replace(source, Path(f"{path}.{index + 1}"))
    os.replace(path, Path(f"{path}.1"))
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--max-mb", type=float, default=5.0)
    parser.add_argument("--generations", type=int, default=DEFAULT_GENERATIONS)
    arguments = parser.parse_args(argv)

    rotated = []
    for name in DEFAULT_LOG_NAMES:
        result = rotate_if_large(
            arguments.log_dir / name,
            max_bytes=int(arguments.max_mb * 1024 * 1024),
            generations=arguments.generations,
        )
        if result is not None:
            rotated.append(name)
    print("회전된 로그: " + (", ".join(rotated) if rotated else "없음"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
