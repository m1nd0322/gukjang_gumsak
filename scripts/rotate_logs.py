#!/usr/bin/env python3
"""임계값을 넘은 로그 파일을 세대 교체 방식으로 회전한다.

launchd가 표준 출력을 계속 덧붙이는 구조라 별도 설정 없이는 로그가
무한히 커진다. ``rotate_if_large``는 현재 파일의 inode를 유지한 채
백업본을 만들고 비우므로 launchd가 이미 연 표준 출력 디스크립터도
계속 원래 로그 파일을 가리킨다.

실행: python scripts/rotate_logs.py [--max-mb 5] [--generations 3]
"""

import argparse
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = REPO_DIR / ".omx" / "logs"
DEFAULT_LOG_NAMES = ("web.log", "daily-refresh.log", "db-backup.log")
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_GENERATIONS = 3
ROTATION_LOCK_TIMEOUT_SECONDS = 30
ROTATION_LOCK_STALE_AFTER_SECONDS = 300


@contextmanager
def _rotation_lock(path):
    """동일 로그에 대한 여러 회전 프로세스를 직렬화한다."""
    lock_path = path.with_name(f".{path.name}.rotate.lock")
    deadline = time.monotonic() + ROTATION_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            os.mkdir(lock_path)
        except FileExistsError:
            try:
                lock_age = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue
            if lock_age >= ROTATION_LOCK_STALE_AFTER_SECONDS:
                try:
                    os.rmdir(lock_path)
                except OSError:
                    pass
                else:
                    continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"로그 회전 잠금 대기 시간 초과: {lock_path}")
            time.sleep(0.05)
        else:
            try:
                yield
            finally:
                try:
                    os.rmdir(lock_path)
                except FileNotFoundError:
                    pass
            return


def _remove_extra_generations(path, generations):
    """요청한 개수보다 높은 번호의 오래된 세대를 정리한다."""
    prefix = f"{path.name}."
    for candidate in path.parent.glob(f"{path.name}.*"):
        suffix = candidate.name[len(prefix):]
        if suffix.isdigit() and int(suffix) >= generations:
            os.unlink(candidate)


def rotate_if_large(
    path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    generations: int = DEFAULT_GENERATIONS,
):
    """파일이 max_bytes를 넘으면 오래된 세대부터 밀어내며 회전한다.

    web.log → web.log.1 → web.log.2 순으로 밀고 마지막 세대는 삭제한다.
    현재 파일은 rename하지 않고 내용을 비워 열린 파일 디스크립터를
    보존한다. 회전이 일어나면 원래 경로를, 기준 이하이거나 없으면
    None을 반환한다.
    """
    path = Path(path)
    generations = max(1, int(generations))
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    if size <= max(1, int(max_bytes)):
        return None

    with _rotation_lock(path):
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return None
        if size <= max(1, int(max_bytes)):
            return None

        _remove_extra_generations(path, generations)
        for index in range(generations - 1, 0, -1):
            source = Path(f"{path}.{index}")
            if source.exists():
                os.replace(source, Path(f"{path}.{index + 1}"))

        first_generation = Path(f"{path}.1")
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.rotate.tmp"
        )
        try:
            with path.open("rb") as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, first_generation)

            # launchd와 기존 로거가 잡고 있는 inode를 유지한다.
            with path.open("r+b") as current:
                current.truncate(0)
                current.flush()
                os.fsync(current.fileno())
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
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
