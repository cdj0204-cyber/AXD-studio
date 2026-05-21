"""
VOLUME_X  —  Surface Offset Designer
AXD Lab  |  v1.0.0

엔지니어 하드웨어 데이터를 기반으로 제품 디자인용
오프셋 볼륨을 자동 생성하는 산업디자이너 전용 툴.

실행:  python main.py
설치:  pip install -r requirements.txt
"""

import sys
import os

# ── PyQt5 고DPI 설정 (import 전에 해야 함) ────────────────────
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

from PyQt5.QtWidgets import QApplication, QSplashScreen, QLabel
from PyQt5.QtGui import QPalette, QColor, QFont, QPixmap
from PyQt5.QtCore import Qt, QTimer


# ─────────────────────────────────────────────────────────────
# 다크 팔레트
# ─────────────────────────────────────────────────────────────
def apply_dark_palette(app: QApplication):
    app.setStyle("Fusion")

    pal = QPalette()
    set = pal.setColor

    set(QPalette.Window,          QColor(11, 11, 24))
    set(QPalette.WindowText,      QColor(208, 208, 232))
    set(QPalette.Base,            QColor(14, 14, 28))
    set(QPalette.AlternateBase,   QColor(20, 20, 40))
    set(QPalette.ToolTipBase,     QColor(30, 30, 55))
    set(QPalette.ToolTipText,     QColor(208, 208, 232))
    set(QPalette.Text,            QColor(208, 208, 232))
    set(QPalette.Button,          QColor(20, 20, 40))
    set(QPalette.ButtonText,      QColor(208, 208, 232))
    set(QPalette.BrightText,      QColor(255, 70, 100))
    set(QPalette.Link,            QColor(0, 180, 216))
    set(QPalette.Highlight,       QColor(0, 180, 216))
    set(QPalette.HighlightedText, QColor(0, 0, 0))

    set(QPalette.Disabled, QPalette.Text,       QColor(80, 80, 110))
    set(QPalette.Disabled, QPalette.ButtonText, QColor(80, 80, 110))
    set(QPalette.Disabled, QPalette.WindowText, QColor(80, 80, 110))

    app.setPalette(pal)


# ─────────────────────────────────────────────────────────────
# 기본 폰트
# ─────────────────────────────────────────────────────────────
def apply_font(app: QApplication):
    font = QFont()
    # 한글 폰트 우선순위
    for name in ["Pretendard", "Noto Sans KR", "Apple SD Gothic Neo",
                 "Malgun Gothic", "Segoe UI"]:
        font.setFamily(name)
        if QFont(name).exactMatch():
            break
    font.setPixelSize(13)
    app.setFont(font)


# ─────────────────────────────────────────────────────────────
# 의존성 사전 점검
# ─────────────────────────────────────────────────────────────
def check_deps() -> list[str]:
    missing = []
    for pkg, imp in [
        ("PyQt5",      "PyQt5"),
        ("pyvista",    "pyvista"),
        ("trimesh",    "trimesh"),
        ("numpy",      "numpy"),
        ("scipy",      "scipy"),
    ]:
        try:
            __import__(imp)
        except ImportError:
            missing.append(pkg)
    return missing


# ─────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────
def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps,    True)

    app = QApplication(sys.argv)
    apply_dark_palette(app)
    apply_font(app)

    # 의존성 체크
    missing = check_deps()
    if missing:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.critical(
            None,
            "패키지 누락",
            f"다음 패키지가 필요합니다:\n\n"
            + "\n".join(f"  •  {p}" for p in missing)
            + "\n\n터미널에서 실행:\n  pip install -r requirements.txt",
        )
        sys.exit(1)

    # 메인 윈도우
    from ui.main_window import MainWindow
    win = MainWindow()
    win.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
