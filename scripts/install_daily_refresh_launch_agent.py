#!/usr/bin/env python3
"""현재 저장소의 07:00 갱신 작업을 macOS LaunchAgent로 등록한다."""

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LABEL = "com.songhear.gukjang-gumsak.daily-refresh"


def build_launch_agent(repo_dir, uv_path):
    repo_dir = Path(repo_dir)
    uv_path = Path(uv_path)
    log_dir = repo_dir / ".omx" / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(uv_path),
            "run",
            "--isolated",
            "--managed-python",
            "--python",
            "3.11",
            "--with-requirements",
            str(repo_dir / "requirements.txt"),
            "python",
            str(repo_dir / "daily_refresh.py"),
        ],
        "WorkingDirectory": str(repo_dir),
        "RunAtLoad": True,
        "StartCalendarInterval": {"Hour": 7, "Minute": 0},
        "ProcessType": "Background",
        "StandardOutPath": str(log_dir / "daily-refresh.log"),
        "StandardErrorPath": str(log_dir / "daily-refresh.log"),
    }


def install():
    if sys.platform != "darwin":
        raise SystemExit("이 설치 스크립트는 macOS에서만 사용할 수 있습니다.")

    repo_dir = Path(__file__).resolve().parents[1]
    uv_path = shutil.which("uv")
    if not uv_path:
        raise SystemExit("uv 실행 파일을 찾지 못했습니다.")

    config = build_launch_agent(repo_dir, uv_path)
    Path(config["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / f"{LABEL}.plist"

    with tempfile.NamedTemporaryFile(
        dir=launch_agents,
        prefix=f".{LABEL}.",
        suffix=".plist",
        delete=False,
    ) as temporary:
        plistlib.dump(config, temporary, sort_keys=False)
        temporary_path = Path(temporary.name)
    temporary_path.chmod(0o644)
    os.replace(temporary_path, plist_path)

    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(plist_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        check=True,
    )
    print(f"등록 완료: {plist_path}")
    print("실행 시각: 매일 07:00 (로그: .omx/logs/daily-refresh.log)")


if __name__ == "__main__":
    install()
