#!/usr/bin/env python3
"""stock_data.duckdb의 검증된 사본을 만들고 오래된 백업을 정리한다.

단일 파일인 DuckDB 데이터베이스가 손상되면 전체 가격 이력을 잃으므로
날짜별 사본을 롤링으로 보관한다. 실행 서버가 같은 파일을 쓰는 도중
복사하면 사본이 깨질 수 있어, 복사 후 사본을 직접 열어 WAL을 반영하고
핵심 테이블 조회로 무결성을 확인한다. 검증에 실패하면 사본을 버리고
재시도하며, 같은 날짜의 백업이 이미 있으면 건너뛴다.

실행: python scripts/backup_stock_db.py [--keep 12]
"""

import argparse
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_DIR / "stock_data.duckdb"
DEFAULT_BACKUP_DIR = REPO_DIR / "backups"
DEFAULT_KEEP = 12
VERIFY_TABLES = ("daily_prices", "ticker_map", "index_prices", "screening_results")
COPY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 10


def backup_prefix(now=None) -> str:
    """백업 파일 이름에 쓰는 날짜 접두사를 반환한다."""
    moment = now or datetime.now()
    return f"stock_data_{moment.strftime('%Y%m%d')}.duckdb"


def _verify_backup(path: Path):
    """사본이 온전한 DuckDB인지 확인하고 WAL을 본체에 반영한다."""
    import duckdb

    connection = duckdb.connect(str(path))
    try:
        for table in VERIFY_TABLES:
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


def create_backup(
    db_path: Path,
    backup_dir: Path,
    *,
    keep: int = DEFAULT_KEEP,
    now=None,
    attempts: int = COPY_ATTEMPTS,
    retry_delay: float = RETRY_DELAY_SECONDS,
) -> tuple[Path | None, str]:
    """검증된 일일 백업을 만들고 (경로, 결과)를 반환한다.

    결과는 "created" / "exists" / "missing" / "failed" 중 하나다.
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    if not db_path.exists():
        return None, "missing"

    target = backup_dir / backup_prefix(now)
    if target.exists():
        return target, "exists"

    backup_dir.mkdir(parents=True, exist_ok=True)
    last_error = None
    for attempt in range(1, max(1, int(attempts)) + 1):
        temporary = backup_dir / f".{target.name}.{os.getpid()}.tmp"
        try:
            shutil.copy2(db_path, temporary)
            wal_path = db_path.with_name(db_path.name + ".wal")
            if wal_path.exists():
                shutil.copy2(wal_path, temporary.with_name(temporary.name + ".wal"))
            _verify_backup(temporary)
            leftover_wal = temporary.with_name(temporary.name + ".wal")
            if leftover_wal.exists():
                os.unlink(leftover_wal)
            os.replace(temporary, target)
            _prune_old_backups(backup_dir, keep)
            return target, "created"
        except Exception as exc:
            last_error = exc
            for leftover in (
                temporary,
                temporary.with_name(temporary.name + ".wal"),
            ):
                try:
                    os.unlink(leftover)
                except FileNotFoundError:
                    pass
            if attempt < int(attempts):
                time.sleep(max(0.0, float(retry_delay)))

    print(f"❌ 백업 검증 실패 ({attempts}회 시도): {last_error}", file=sys.stderr)
    return None, "failed"


def _prune_old_backups(backup_dir: Path, keep: int) -> list[Path]:
    """최근 keep개만 남기고 이전 백업 파일을 삭제한다."""
    backups = sorted(backup_dir.glob("stock_data_*.duckdb"))
    removed = []
    for stale in backups[: max(0, len(backups) - max(0, int(keep)))]:
        os.unlink(stale)
        removed.append(stale)
    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    arguments = parser.parse_args(argv)

    path, result = create_backup(
        arguments.db,
        arguments.backup_dir,
        keep=arguments.keep,
    )
    if result == "created":
        print(f"✅ 백업 완료: {path}")
        return 0
    if result == "exists":
        print(f"오늘 백업이 이미 있습니다: {path}")
        return 0
    if result == "missing":
        print(f"원본 데이터베이스가 없습니다: {arguments.db}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
