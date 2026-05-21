"""
VOLUME_X - OCC Bridge
pythonocc-core를 사용할 수 있는 Python 인터프리터를 자동 탐지하여
occ_worker.py를 subprocess로 실행합니다.

지원 탐지 경로:
  - Windows py launcher (py -3.12, py -3.11, py -3.13)
  - 일반적인 설치 경로 (C:\\Python312, Miniconda, Anaconda, venv 등)
  - VOLUME_X\\occ_env\\ (전용 임베디드 환경)
"""

import os
import subprocess

_OCC_CMD   = None   # 탐지된 Python 명령
_CHECKED   = False  # 탐지 완료 여부

WORKER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "occ_worker.py")

_CHECK_CODE = (
    "try:\n"
    "  from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape\n"
    "except ImportError:\n"
    "  from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape\n"
    "print('occ_ok')"
)

def _probe(cmd: list[str]) -> bool:
    """해당 Python 명령에 pythonocc-core가 설치되어 있는지 확인"""
    try:
        r = subprocess.run(
            cmd + ["-c", _CHECK_CODE],
            capture_output=True, text=True, timeout=8,
        )
        return r.returncode == 0 and "occ_ok" in r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def detect_occ_python() -> list[str] | None:
    """pythonocc-core가 있는 Python 명령을 반환. 없으면 None."""
    global _OCC_CMD, _CHECKED
    if _CHECKED:
        return _OCC_CMD
    _CHECKED = True

    home = os.path.expanduser("~")
    vol  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # VOLUME_X 루트

    candidates: list[list[str]] = [
        # Windows py launcher (가장 일반적)
        ["py", "-3.12"],
        ["py", "-3.11"],
        ["py", "-3.13"],
        # 직접 실행 파일
        ["python3.12"],
        ["python3.11"],
        ["python3.13"],
        # 일반적인 설치 경로
        [r"C:\Python312\python.exe"],
        [r"C:\Python311\python.exe"],
        [r"C:\Python313\python.exe"],
        # VOLUME_X 전용 OCC 환경
        [os.path.join(vol, "occ_env", "python.exe")],
        # Miniconda / Anaconda 환경
        [os.path.join(r"C:\ProgramData\Miniconda3", "envs", "occ", "python.exe")],
        [os.path.join(r"C:\ProgramData\Anaconda3",  "envs", "occ", "python.exe")],
        [os.path.join(home, "miniconda3", "envs", "occ", "python.exe")],
        [os.path.join(home, "anaconda3",  "envs", "occ", "python.exe")],
        [os.path.join(home, "Miniconda3", "envs", "occ", "python.exe")],
        [os.path.join(home, "Anaconda3",  "envs", "occ", "python.exe")],
        # conda base (혹시 거기에 설치된 경우)
        [os.path.join(r"C:\ProgramData\Miniconda3", "python.exe")],
        [os.path.join(home, "miniconda3", "python.exe")],
    ]

    for cmd in candidates:
        # 경로가 명시된 경우 파일 존재 여부 먼저 확인
        if len(cmd) == 1 and not cmd[0].startswith("py") and not cmd[0].startswith("python3"):
            if not os.path.exists(cmd[0]):
                continue
        if _probe(cmd):
            _OCC_CMD = cmd
            return cmd

    return None


def occ_available() -> bool:
    """NURBS 오프셋 사용 가능 여부"""
    return detect_occ_python() is not None


def occ_setup_guide() -> str:
    """NURBS 모드 활성화를 위한 설치 안내 문자열"""
    return (
        "NURBS 모드를 사용하려면 OCC 라이브러리가 필요합니다.\n\n"
        "방법 1 — Python 3.12 + cadquery-ocp (권장):\n"
        "  1) https://python.org/downloads/release/python-31210/ 에서\n"
        "     Python 3.12 설치 (현재 Python 3.14와 공존 가능)\n"
        "  2) py -3.12 -m pip install cadquery-ocp\n"
        "  3) VOLUME_X 재시작\n\n"
        "방법 2 — conda + pythonocc-core:\n"
        "  1) https://docs.conda.io/en/latest/miniconda.html 에서 Miniconda 설치\n"
        "  2) conda create -n occ python=3.12\n"
        "  3) conda activate occ\n"
        "  4) conda install -c conda-forge pythonocc-core\n"
        "  5) VOLUME_X 재시작\n\n"
        "NURBS 미설치 시 기존 메쉬 방식으로 오프셋됩니다."
    )
