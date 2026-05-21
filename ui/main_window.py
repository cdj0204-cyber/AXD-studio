"""
VOLUME_X - Main Application Window
PyQt5 + pyvistaqt 기반 다크 테마 인터페이스
"""

import os
import sys

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
    QLabel, QPushButton, QDoubleSpinBox, QSpinBox, QSlider,
    QCheckBox, QRadioButton, QButtonGroup,
    QProgressBar, QFrame, QFileDialog, QMessageBox,
    QSizePolicy, QSpacerItem, QScrollArea,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QMimeData
from PyQt5.QtGui import QFont, QDragEnterEvent, QDropEvent, QColor

import pyvista as pv

try:
    import pyvistaqt as pvqt
    PYVISTAQT_OK = True
except ImportError:
    PYVISTAQT_OK = False

from core.loader    import load_mesh_file
from core.processor import offset_mesh, smooth_mesh, get_mesh_info
from core.exporter  import export_mesh
from core.occ_bridge import occ_available, detect_occ_python, occ_setup_guide, WORKER_PATH


# ═══════════════════════════════════════════════════════════════
# 색상 팔레트
# ═══════════════════════════════════════════════════════════════
C_BG        = "#0b0b18"   # 최외곽 배경
C_PANEL     = "#0e0e1e"   # 사이드 패널
C_CARD      = "#141428"   # 카드/섹션 배경
C_BORDER    = "#1e1e38"   # 구분선
C_ACCENT    = "#00b4d8"   # 주 강조색 (시안)
C_ACCENT2   = "#0077aa"   # 보조 강조색
C_SUCCESS   = "#44cc88"   # 성공
C_WARNING   = "#ffaa44"   # 경고
C_ERROR     = "#ff4466"   # 오류
C_TEXT      = "#d0d0e8"   # 본문
C_MUTED     = "#555577"   # 흐린 텍스트
C_DISABLED  = "#2a2a40"   # 비활성


# ═══════════════════════════════════════════════════════════════
# Worker Thread
# ═══════════════════════════════════════════════════════════════
class ProcessWorker(QThread):
    progress = pyqtSignal(int, str)
    # finished(mesh, occ_step_path)  — occ_step_path가 빈 문자열이면 메쉬 모드
    finished = pyqtSignal(object, str)
    error    = pyqtSignal(str)

    def __init__(self, mesh, offset_dist, smooth_iter, pass_band,
                 step_path="", occ_cmd=None):
        super().__init__()
        self.mesh        = mesh
        self.offset_dist = offset_dist
        self.smooth_iter = smooth_iter
        self.pass_band   = pass_band
        self.step_path   = step_path   # 원본 STEP 경로 (NURBS 모드 시)
        self.occ_cmd     = occ_cmd     # OCC Python 명령 리스트

    def run(self):
        try:
            if self.step_path and self.occ_cmd:
                self._run_nurbs()
            else:
                self._run_mesh()
        except Exception as e:
            self.error.emit(str(e))

    # ── NURBS 경로 ────────────────────────────────────────────
    def _run_nurbs(self):
        import subprocess
        import json
        import tempfile

        # 임시 출력 STEP 파일
        tmp = tempfile.NamedTemporaryFile(suffix=".step", delete=False)
        tmp.close()
        tmp_path = tmp.name

        cmd = self.occ_cmd + [
            WORKER_PATH,
            "--input",    str(self.step_path),
            "--output",   tmp_path,
            "--distance", str(self.offset_dist),
        ]

        self.progress.emit(10, "NURBS 오프셋 준비 중 (OCC)...")

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        result_mesh_data = None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                s   = msg.get("status", "")
                if s == "loading":
                    self.progress.emit(20, "STEP 파일 분석 중...")
                elif s == "offsetting":
                    self.progress.emit(40, f"NURBS 오프셋 연산 중 ({self.offset_dist:.1f}mm)...")
                elif s == "exporting_step":
                    self.progress.emit(70, "NURBS STEP 파일 생성 중...")
                elif s == "meshing":
                    self.progress.emit(85, "뷰어용 메쉬 변환 중...")
                elif s == "done":
                    result_mesh_data = msg.get("mesh")
                    self.progress.emit(100, "완료!")
                elif "error" in msg:
                    raise RuntimeError(msg["error"])
            except json.JSONDecodeError:
                pass

        proc.wait()
        if proc.returncode != 0:
            stderr_out = proc.stderr.read()
            raise RuntimeError(f"OCC 오프셋 실패:\n{stderr_out}")

        if not result_mesh_data:
            raise RuntimeError("OCC 워커가 메쉬 데이터를 반환하지 않았습니다.")

        import numpy as np
        pts   = np.array(result_mesh_data["points"], dtype=np.float64)
        tris  = np.array(result_mesh_data["faces"],  dtype=np.int64)
        faces = np.hstack([np.full((len(tris), 1), 3, dtype=np.int64), tris])
        poly  = pv.PolyData(pts, faces.ravel())

        self.finished.emit(poly, tmp_path)

    # ── 메쉬 경로 (기존) ────────────────────────────────────
    def _run_mesh(self):
        self.progress.emit(10, "메쉬 준비 중...")
        mesh = self.mesh.copy()
        mesh.clear_data()
        mesh = mesh.triangulate()

        self.progress.emit(30, f"{self.offset_dist:.1f}mm 오프셋 계산 중...")
        result = offset_mesh(mesh, self.offset_dist)

        self.progress.emit(65, f"스무딩 적용 중 ({self.smooth_iter}회)...")
        result = smooth_mesh(result, self.smooth_iter, self.pass_band)

        self.progress.emit(85, "메쉬 정리 중...")
        result = result.clean()

        self.progress.emit(100, "완료!")
        self.finished.emit(result, "")


# ═══════════════════════════════════════════════════════════════
# Drop Zone Widget
# ═══════════════════════════════════════════════════════════════
class DropZone(QLabel):
    file_dropped = pyqtSignal(str)

    _STYLE_IDLE = f"""
        QLabel {{
            border: 2px dashed {C_BORDER};
            border-radius: 10px;
            color: {C_MUTED};
            font-size: 12px;
            line-height: 1.6;
            padding: 24px 12px;
            background: rgba(20, 20, 40, 0.4);
        }}
        QLabel:hover {{
            border-color: {C_ACCENT};
            color: {C_ACCENT};
            background: rgba(0, 180, 216, 0.04);
        }}
    """
    _STYLE_ACTIVE = f"""
        QLabel {{
            border: 2px dashed {C_ACCENT};
            border-radius: 10px;
            color: {C_ACCENT};
            font-size: 12px;
            padding: 24px 12px;
            background: rgba(0, 180, 216, 0.08);
        }}
    """
    _STYLE_LOADED = f"""
        QLabel {{
            border: 2px solid {C_ACCENT};
            border-radius: 10px;
            color: {C_ACCENT};
            font-size: 11px;
            padding: 16px 12px;
            background: rgba(0, 180, 216, 0.06);
        }}
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(130)
        self.setCursor(Qt.PointingHandCursor)
        self._set_idle()

    def _set_idle(self):
        self.setText("드래그 앤 드롭\n또는 클릭하여 파일 선택\n\nSTL  ·  OBJ  ·  FBX  ·  STEP")
        self.setStyleSheet(self._STYLE_IDLE)

    def set_loaded(self, filename):
        self.setText(f"✓  {filename}\n\n다른 파일을 드롭하거나 클릭하여 교체")
        self.setStyleSheet(self._STYLE_LOADED)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.accept()
            self.setStyleSheet(self._STYLE_ACTIVE)
        else:
            e.ignore()

    def dragLeaveEvent(self, _):
        self.setStyleSheet(self._STYLE_IDLE)

    def dropEvent(self, e: QDropEvent):
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self.file_dropped.emit(path)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            path, _ = QFileDialog.getOpenFileName(
                self, "3D 파일 열기", "",
                "3D Files (*.stl *.obj *.ply *.fbx *.step *.stp *.iges *.igs);;"
                "STL (*.stl);;OBJ (*.obj);;PLY (*.ply);;All Files (*)",
            )
            if path:
                self.file_dropped.emit(path)


# ═══════════════════════════════════════════════════════════════
# Main Window
# ═══════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.current_mesh      = None
        self.current_step_path = ""    # STEP 입력 경로 (NURBS 모드용)
        self.offset_result     = None
        self.offset_step_path  = ""    # OCC 생성 STEP 경로 (NURBS 결과)
        self.worker            = None
        self._occ_cmd          = None  # 탐지된 OCC Python 명령
        self._setup_ui()
        # OCC 백그라운드 탐지
        self._detect_occ_async()

    # ─────────────────────────────────────────────────────────
    # UI 초기화
    # ─────────────────────────────────────────────────────────
    def _setup_ui(self):
        self.setWindowTitle("VOLUME_X  —  Surface Offset Designer")
        self.resize(1440, 900)
        self.setMinimumSize(900, 600)
        self.setStyleSheet(f"background:{C_BG};")

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 왼쪽 패널
        side = self._build_side_panel()
        side.setFixedWidth(290)
        root.addWidget(side)

        # 구분선
        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{C_BORDER};")
        root.addWidget(sep)

        # 3D 뷰포트
        viewport = self._build_viewport()
        root.addWidget(viewport, stretch=1)

    # ─────────────────────────────────────────────────────────
    # 사이드 패널
    # ─────────────────────────────────────────────────────────
    def _build_side_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(f"background:{C_PANEL};")

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("border:none; background:transparent;")

        outer = QWidget()
        outer.setFixedWidth(290)
        outer.setStyleSheet(f"background:{C_PANEL};")
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(18, 20, 18, 20)
        lay.setSpacing(10)

        # ── 헤더 ──────────────────────────────────────────
        title = QLabel("VOLUME_X")
        title.setStyleSheet(
            f"font-size:24px; font-weight:900; color:{C_ACCENT}; letter-spacing:4px;"
        )
        lay.addWidget(title)

        sub = QLabel("Surface Offset Designer  |  AXD Lab")
        sub.setStyleSheet(f"font-size:9px; color:{C_MUTED}; margin-bottom:4px;")
        lay.addWidget(sub)

        lay.addWidget(self._divider())

        # ── 파일 업로드 ───────────────────────────────────
        lay.addWidget(self._section_lbl("01  파일 업로드"))

        self.drop_zone = DropZone()
        self.drop_zone.file_dropped.connect(self._on_file_drop)
        lay.addWidget(self.drop_zone)

        # NURBS 상태 뱃지
        self.lbl_nurbs = QLabel("⬡  NURBS 모드 대기 중...")
        self.lbl_nurbs.setStyleSheet(
            f"font-size:9px; color:{C_MUTED}; padding:3px 0;"
        )
        lay.addWidget(self.lbl_nurbs)

        # 메쉬 정보 카드
        self.info_card = self._build_info_card()
        self.info_card.setVisible(False)
        lay.addWidget(self.info_card)

        lay.addWidget(self._divider())

        # ── 오프셋 설정 ───────────────────────────────────
        lay.addWidget(self._section_lbl("02  오프셋 간격"))

        # 스피너 + 레이블 행
        off_row = QHBoxLayout()
        off_lbl = QLabel("간격")
        off_lbl.setStyleSheet(f"font-size:12px; color:{C_TEXT};")
        off_row.addWidget(off_lbl)
        off_row.addStretch()

        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(1.0, 100.0)
        self.offset_spin.setValue(3.0)
        self.offset_spin.setSingleStep(0.5)
        self.offset_spin.setDecimals(1)
        self.offset_spin.setSuffix(" mm")
        self.offset_spin.setFixedWidth(110)
        self.offset_spin.setStyleSheet(self._spin_style())
        off_row.addWidget(self.offset_spin)
        lay.addLayout(off_row)

        # 슬라이더 (10 = 1.0mm, 1000 = 100.0mm)
        self.offset_slider = QSlider(Qt.Horizontal)
        self.offset_slider.setRange(10, 1000)
        self.offset_slider.setValue(30)
        self.offset_slider.setStyleSheet(self._slider_style(C_ACCENT))
        lay.addWidget(self.offset_slider)

        # 최소·최대 힌트
        hint_row = QHBoxLayout()
        hint_row.addWidget(self._muted("1.0 mm"))
        hint_row.addStretch()
        hint_row.addWidget(self._muted("100.0 mm"))
        lay.addLayout(hint_row)

        # 신호 연결 (순환 방지)
        self.offset_slider.valueChanged.connect(self._slider_to_spin)
        self.offset_spin.valueChanged.connect(self._spin_to_slider)

        lay.addWidget(self._divider())

        # ── 스무딩 설정 ───────────────────────────────────
        self.lbl_smooth_section = self._section_lbl("03  스무딩 (Taubin)")
        lay.addWidget(self.lbl_smooth_section)

        sm_row = QHBoxLayout()
        sm_row.addWidget(QLabel("반복 횟수") if False else self._label("반복 횟수"))
        sm_row.addStretch()

        self.smooth_spin = QSpinBox()
        self.smooth_spin.setRange(0, 500)
        self.smooth_spin.setValue(50)
        self.smooth_spin.setSingleStep(10)
        self.smooth_spin.setFixedWidth(90)
        self.smooth_spin.setStyleSheet(self._spin_style())
        sm_row.addWidget(self.smooth_spin)
        lay.addLayout(sm_row)

        pb_row = QHBoxLayout()
        pb_row.addWidget(self._label("Pass Band"))
        pb_row.addStretch()

        self.passband_spin = QDoubleSpinBox()
        self.passband_spin.setRange(0.01, 0.5)
        self.passband_spin.setValue(0.1)
        self.passband_spin.setSingleStep(0.01)
        self.passband_spin.setDecimals(2)
        self.passband_spin.setFixedWidth(90)
        self.passband_spin.setStyleSheet(self._spin_style())
        pb_row.addWidget(self.passband_spin)
        lay.addLayout(pb_row)

        lay.addWidget(self._divider())

        # ── 뷰 옵션 ───────────────────────────────────────
        lay.addWidget(self._section_lbl("04  화면 옵션"))

        self.cb_show_orig = QCheckBox("원본 메쉬 표시")
        self.cb_show_orig.setChecked(True)
        self.cb_show_orig.setStyleSheet(self._cb_style())
        self.cb_show_orig.toggled.connect(self._refresh_view)

        self.cb_wireframe = QCheckBox("와이어프레임 모드")
        self.cb_wireframe.setStyleSheet(self._cb_style())
        self.cb_wireframe.toggled.connect(self._refresh_view)

        self.cb_edges = QCheckBox("엣지 표시")
        self.cb_edges.setStyleSheet(self._cb_style())
        self.cb_edges.toggled.connect(self._refresh_view)

        lay.addWidget(self.cb_show_orig)
        lay.addWidget(self.cb_wireframe)
        lay.addWidget(self.cb_edges)

        # ── 쉐이딩 옵션 ──────────────────────────────────
        lay.addWidget(self._muted("쉐이딩"))

        self.shade_group = QButtonGroup(self)
        shade_row = QHBoxLayout()
        shade_row.setSpacing(4)

        self.rb_smooth = QRadioButton("스무스")
        self.rb_flat   = QRadioButton("플랫")
        self.rb_shadow = QRadioButton("음영 전용")
        self.rb_smooth.setChecked(True)

        for idx, rb in enumerate([self.rb_smooth, self.rb_flat, self.rb_shadow]):
            rb.setStyleSheet(self._rb_style())
            rb.toggled.connect(self._refresh_view)
            self.shade_group.addButton(rb, idx)
            shade_row.addWidget(rb)

        lay.addLayout(shade_row)

        lay.addWidget(self._divider())

        # ── 실행 버튼 ─────────────────────────────────────
        self.btn_generate = QPushButton("⚡  GENERATE  VOLUME")
        self.btn_generate.setEnabled(False)
        self.btn_generate.setMinimumHeight(46)
        self.btn_generate.setStyleSheet(self._btn_primary())
        self.btn_generate.clicked.connect(self._on_generate)
        lay.addWidget(self.btn_generate)

        # 진행바
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress.setFixedHeight(14)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(self._progressbar_style())
        lay.addWidget(self.progress)

        self.lbl_status = QLabel("")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet(f"font-size:10px; color:{C_ACCENT};")
        lay.addWidget(self.lbl_status)

        # 내보내기 버튼
        self.btn_export = QPushButton("💾  내보내기  (STEP / STL / OBJ)")
        self.btn_export.setEnabled(False)
        self.btn_export.setMinimumHeight(38)
        self.btn_export.setStyleSheet(self._btn_success())
        self.btn_export.clicked.connect(self._on_export)
        lay.addWidget(self.btn_export)

        lay.addStretch()

        # NURBS 안내 버튼
        self.btn_nurbs_info = QPushButton("⬡  NURBS 설정 안내")
        self.btn_nurbs_info.setFixedHeight(28)
        self.btn_nurbs_info.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C_MUTED};
                font-size: 9px;
                border: 1px solid {C_BORDER};
                border-radius: 4px;
            }}
            QPushButton:hover {{
                color: {C_ACCENT};
                border-color: {C_ACCENT};
            }}
        """)
        self.btn_nurbs_info.clicked.connect(self._show_nurbs_guide)
        lay.addWidget(self.btn_nurbs_info)

        # 하단 버전
        ver = QLabel("VOLUME_X  v1.0.0   |   © AXD Lab")
        ver.setAlignment(Qt.AlignCenter)
        ver.setStyleSheet(f"font-size:9px; color:{C_MUTED};")
        lay.addWidget(ver)

        return outer

    def _build_info_card(self) -> QWidget:
        card = QWidget()
        card.setStyleSheet(
            f"background:{C_CARD}; border-radius:8px; border:1px solid {C_BORDER};"
        )
        g = QGridLayout(card)
        g.setContentsMargins(12, 10, 12, 10)
        g.setSpacing(4)

        def row(label, attr):
            lbl = self._muted(label)
            val = QLabel("—")
            val.setStyleSheet(f"font-size:11px; color:{C_TEXT}; font-weight:bold;")
            setattr(self, attr, val)
            return lbl, val

        for i, (l, a) in enumerate([
            ("정점 수",    "stat_verts"),
            ("면 수",      "stat_faces"),
            ("크기 X",     "stat_x"),
            ("크기 Y",     "stat_y"),
            ("크기 Z",     "stat_z"),
        ]):
            lbl, val = row(l, a)
            g.addWidget(lbl, i, 0)
            g.addWidget(val, i, 1)

        return card

    # ─────────────────────────────────────────────────────────
    # 3D 뷰포트
    # ─────────────────────────────────────────────────────────
    def _build_viewport(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background:{C_BG};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        if PYVISTAQT_OK:
            self.plotter = pvqt.QtInteractor(container)
            self.plotter.set_background([0.04, 0.04, 0.09])
            self.plotter.enable_anti_aliasing()
            layout.addWidget(self.plotter)
            self._init_plotter()
        else:
            warn = QLabel(
                "⚠  3D 뷰어를 사용하려면 pyvistaqt 를 설치하세요\n\n"
                "  pip install pyvistaqt\n\n"
                "설치 후 재시작하면 뷰어가 활성화됩니다.\n"
                "파일 처리 및 내보내기는 정상 작동합니다."
            )
            warn.setAlignment(Qt.AlignCenter)
            warn.setStyleSheet(f"color:{C_WARNING}; font-size:14px; line-height:1.8;")
            layout.addWidget(warn)
            self.plotter = None

        return container

    def _init_plotter(self):
        if not self.plotter:
            return
        self.plotter.add_text(
            "파일을 업로드하여 시작하세요",
            position="upper_left",
            font_size=13,
            color="gray",
            name="hint",
        )
        self.plotter.show_axes()
        self.plotter.view_isometric()

    # ─────────────────────────────────────────────────────────
    # OCC / NURBS 탐지
    # ─────────────────────────────────────────────────────────
    def _detect_occ_async(self):
        """백그라운드 스레드에서 OCC Python 탐지"""
        class _OccDetector(QThread):
            detected = pyqtSignal(object)  # list[str] or None
            def run(self):
                self.detected.emit(detect_occ_python())

        self._occ_detector = _OccDetector()
        self._occ_detector.detected.connect(self._on_occ_detected)
        self._occ_detector.start()

    def _on_occ_detected(self, cmd):
        self._occ_cmd = cmd
        if cmd:
            self.lbl_nurbs.setText("⬡  NURBS 모드 준비됨 (OCC)")
            self.lbl_nurbs.setStyleSheet(
                f"font-size:9px; color:{C_SUCCESS}; padding:3px 0;"
            )
        else:
            self.lbl_nurbs.setText("⬡  NURBS 미설치 — 메쉬 방식 사용 중")
            self.lbl_nurbs.setStyleSheet(
                f"font-size:9px; color:{C_MUTED}; padding:3px 0;"
            )

    def _show_nurbs_guide(self):
        QMessageBox.information(self, "NURBS 모드 설정 안내", occ_setup_guide())

    # ─────────────────────────────────────────────────────────
    # 슬라이더 ↔ 스피너 동기화
    # ─────────────────────────────────────────────────────────
    def _slider_to_spin(self, v):
        self.offset_spin.blockSignals(True)
        self.offset_spin.setValue(v / 10.0)
        self.offset_spin.blockSignals(False)

    def _spin_to_slider(self, v):
        self.offset_slider.blockSignals(True)
        self.offset_slider.setValue(int(v * 10))
        self.offset_slider.blockSignals(False)

    # ─────────────────────────────────────────────────────────
    # 파일 로드
    # ─────────────────────────────────────────────────────────
    def _on_file_drop(self, filepath: str):
        self.lbl_status.setText("파일 로딩 중…")
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()

        try:
            mesh = load_mesh_file(filepath)
        except Exception as e:
            QMessageBox.critical(self, "파일 오류", str(e))
            self.lbl_status.setText("")
            return

        self.current_mesh      = mesh
        self.offset_result     = None
        self.offset_step_path  = ""

        # STEP 파일이면 경로 저장 (NURBS 모드용)
        ext = os.path.splitext(filepath)[1].lower()
        if ext in (".step", ".stp"):
            self.current_step_path = filepath
            if self._occ_cmd:
                self.lbl_nurbs.setText("⬡  NURBS 모드 활성화 ✓  (AP214 STEP 출력)")
                self.lbl_nurbs.setStyleSheet(
                    f"font-size:9px; color:{C_ACCENT}; padding:3px 0; font-weight:bold;"
                )
                # 스무딩은 메쉬 모드 전용 — 흐리게 표시
                self.lbl_smooth_section.setText("03  스무딩 (메쉬 전용 — NURBS 무시)")
                self.lbl_smooth_section.setStyleSheet(
                    f"font-size:9px; font-weight:bold; color:{C_DISABLED}; letter-spacing:2px;"
                )
                self.smooth_spin.setEnabled(False)
                self.passband_spin.setEnabled(False)
            else:
                self.lbl_nurbs.setText("⬡  STEP 감지 — NURBS 미설치 (메쉬 방식)")
                self.lbl_nurbs.setStyleSheet(
                    f"font-size:9px; color:{C_WARNING}; padding:3px 0;"
                )
                self._reset_smooth_section()
        else:
            self.current_step_path = ""
            self.lbl_nurbs.setText(f"⬡  {ext.upper()[1:]} 파일 — 메쉬 방식")
            self.lbl_nurbs.setStyleSheet(
                f"font-size:9px; color:{C_MUTED}; padding:3px 0;"
            )
            self._reset_smooth_section()

        # 통계 업데이트
        info = get_mesh_info(mesh)
        self.stat_verts.setText(f"{info['n_points']:,}")
        self.stat_faces.setText(f"{info['n_faces']:,}")
        self.stat_x.setText(f"{info['dim_x']:.2f} mm")
        self.stat_y.setText(f"{info['dim_y']:.2f} mm")
        self.stat_z.setText(f"{info['dim_z']:.2f} mm")
        self.info_card.setVisible(True)

        fname = os.path.basename(filepath)
        self.drop_zone.set_loaded(fname)
        self.lbl_status.setText(f"✓  {fname} 로드 완료")
        self.btn_generate.setEnabled(True)

        self._refresh_view()

    def _reset_smooth_section(self):
        """스무딩 섹션을 활성 상태로 복원"""
        self.lbl_smooth_section.setText("03  스무딩 (Taubin)")
        self.lbl_smooth_section.setStyleSheet(
            f"font-size:9px; font-weight:bold; color:{C_MUTED}; letter-spacing:2px;"
        )
        self.smooth_spin.setEnabled(True)
        self.passband_spin.setEnabled(True)

    # ─────────────────────────────────────────────────────────
    # 오프셋 생성
    # ─────────────────────────────────────────────────────────
    def _on_generate(self):
        if self.current_mesh is None:
            return

        self.btn_generate.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.lbl_status.setText("처리 중…")

        use_nurbs = bool(self.current_step_path and self._occ_cmd)
        self.worker = ProcessWorker(
            mesh        = self.current_mesh,
            offset_dist = self.offset_spin.value(),
            smooth_iter = self.smooth_spin.value(),
            pass_band   = self.passband_spin.value(),
            step_path   = self.current_step_path if use_nurbs else "",
            occ_cmd     = self._occ_cmd if use_nurbs else None,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_done)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, pct: int, msg: str):
        self.progress.setValue(pct)
        self.lbl_status.setText(msg)

    def _on_done(self, result, occ_step_path: str):
        # 이전 NURBS 임시 파일 정리
        if self.offset_step_path and self.offset_step_path != occ_step_path:
            try:
                import os as _os
                if _os.path.isfile(self.offset_step_path):
                    _os.unlink(self.offset_step_path)
            except Exception:
                pass

        self.offset_result    = result
        self.offset_step_path = occ_step_path  # NURBS STEP 임시 경로 (비어있으면 메쉬 모드)
        self.progress.setVisible(False)
        if occ_step_path:
            self.lbl_status.setText("✓  NURBS 오프셋 완료 — STEP 저장 가능")
        else:
            self.lbl_status.setText("✓  볼륨 생성 완료!")
        self.btn_generate.setEnabled(True)
        self.btn_export.setEnabled(True)
        self._refresh_view()

    def _on_error(self, msg: str):
        self.progress.setVisible(False)
        self.lbl_status.setText("✗  오류 발생")
        self.btn_generate.setEnabled(True)
        QMessageBox.critical(self, "처리 오류", msg)

    def closeEvent(self, event):
        """앱 종료 시 임시 NURBS STEP 파일 정리"""
        if self.offset_step_path:
            try:
                if os.path.isfile(self.offset_step_path):
                    os.unlink(self.offset_step_path)
            except Exception:
                pass
        super().closeEvent(event)

    # ─────────────────────────────────────────────────────────
    # 내보내기
    # ─────────────────────────────────────────────────────────
    def _on_export(self):
        if self.offset_result is None:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "볼륨 저장", "VOLUME_X_output",
            "STEP (*.step);;STL (*.stl);;OBJ (*.obj);;PLY (*.ply);;All Files (*)",
        )
        if not path:
            return

        ext = os.path.splitext(path)[1].lower()
        try:
            # NURBS 결과이고 STEP 형식으로 저장하는 경우 → 진짜 NURBS STEP 복사
            if self.offset_step_path and ext in (".step", ".stp"):
                import shutil
                shutil.copy2(self.offset_step_path, path)
                QMessageBox.information(
                    self, "저장 완료",
                    f"NURBS STEP 파일 저장 완료\n{path}\n\n"
                    f"• 스키마: AP214 (AUTOMOTIVE_DESIGN)\n"
                    f"• 형식: NURBS B-Spline Surface (삼각형 없음)\n"
                    f"• 호환: Rhino / SolidWorks / CATIA / Fusion360"
                )
            else:
                # 메쉬 방식 내보내기 (기존)
                saved = export_mesh(self.offset_result, path)
                QMessageBox.information(self, "저장 완료", f"파일 저장:\n{saved}")
        except Exception as e:
            QMessageBox.critical(self, "저장 오류", str(e))

    # ─────────────────────────────────────────────────────────
    # 3D 뷰 갱신
    # ─────────────────────────────────────────────────────────
    def _refresh_view(self):
        if not self.plotter:
            return

        self.plotter.clear()

        wireframe  = self.cb_wireframe.isChecked()
        edges      = self.cb_edges.isChecked()
        shade_id   = self.shade_group.checkedId()  # 0=스무스, 1=플랫, 2=음영 전용
        is_shadow  = (shade_id == 2)
        smooth_sh  = (shade_id == 0)               # 스무스=True, 플랫=False

        if is_shadow:
            # ── 음영 전용 모드: 색 없이 명암만으로 형태 표현 ──
            if self.current_mesh is not None and self.cb_show_orig.isChecked():
                if self.offset_result is not None:
                    self.plotter.add_mesh(
                        self.current_mesh,
                        color="#1a1a2a",
                        style="wireframe",
                        opacity=0.12,
                        name="original",
                    )
                else:
                    self.plotter.add_mesh(
                        self.current_mesh,
                        color="#2a2a3e",
                        style="surface",
                        smooth_shading=True,
                        ambient=0.15,
                        diffuse=0.85,
                        specular=0.0,
                        opacity=1.0,
                        name="original",
                    )

            if self.offset_result is not None:
                self.plotter.add_mesh(
                    self.offset_result,
                    color="#1e1e32",          # 거의 검정 — 음영만으로 형태 인식
                    style="surface",
                    smooth_shading=True,
                    ambient=0.08,             # 낮은 ambient → 그림자 깊게
                    diffuse=0.92,             # 높은 diffuse → 명암 대비 강조
                    specular=0.0,
                    opacity=1.0,
                    name="offset",
                )
        else:
            # ── 일반 쉐이딩 모드 (스무스 / 플랫) ──────────────
            if self.current_mesh is not None and self.cb_show_orig.isChecked():
                if self.offset_result is not None:
                    self.plotter.add_mesh(
                        self.current_mesh,
                        color="#6688aa",
                        style="wireframe",
                        opacity=0.3,
                        name="original",
                    )
                else:
                    self.plotter.add_mesh(
                        self.current_mesh,
                        color="#88aacc",
                        opacity=0.9,
                        style="wireframe" if wireframe else "surface",
                        show_edges=edges,
                        smooth_shading=smooth_sh,
                        name="original",
                    )

            if self.offset_result is not None:
                self.plotter.add_mesh(
                    self.offset_result,
                    color=C_ACCENT,
                    opacity=0.88,
                    style="wireframe" if wireframe else "surface",
                    show_edges=edges,
                    smooth_shading=smooth_sh,
                    ambient=0.3,
                    diffuse=0.7,
                    specular=0.1 if smooth_sh else 0.0,
                    name="offset",
                )

        self.plotter.show_axes()
        self.plotter.reset_camera()

    # ─────────────────────────────────────────────────────────
    # 스타일 헬퍼
    # ─────────────────────────────────────────────────────────
    def _divider(self) -> QFrame:
        f = QFrame()
        f.setFixedHeight(1)
        f.setStyleSheet(f"background:{C_BORDER}; margin:4px 0;")
        return f

    def _section_lbl(self, txt: str) -> QLabel:
        lbl = QLabel(txt)
        lbl.setStyleSheet(
            f"font-size:9px; font-weight:bold; color:{C_MUTED}; letter-spacing:2px;"
        )
        return lbl

    def _label(self, txt: str) -> QLabel:
        lbl = QLabel(txt)
        lbl.setStyleSheet(f"font-size:12px; color:{C_TEXT};")
        return lbl

    def _muted(self, txt: str) -> QLabel:
        lbl = QLabel(txt)
        lbl.setStyleSheet(f"font-size:10px; color:{C_MUTED};")
        return lbl

    def _spin_style(self) -> str:
        return f"""
            QDoubleSpinBox, QSpinBox {{
                background: {C_CARD};
                border: 1px solid {C_BORDER};
                border-radius: 5px;
                color: {C_ACCENT};
                font-size: 13px;
                font-weight: bold;
                padding: 4px 8px;
            }}
            QDoubleSpinBox::up-button, QSpinBox::up-button,
            QDoubleSpinBox::down-button, QSpinBox::down-button {{
                background: #1e1e35;
                border: none;
                width: 22px;
            }}
            QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover,
            QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover {{
                background: {C_BORDER};
            }}
        """

    def _slider_style(self, color: str) -> str:
        return f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: {C_CARD};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {color};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {color};
                border: 2px solid {C_BG};
                width: 16px; height: 16px;
                margin: -6px 0;
                border-radius: 8px;
            }}
        """

    def _cb_style(self) -> str:
        return f"""
            QCheckBox {{
                color: {C_TEXT};
                font-size: 11px;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 14px; height: 14px;
                border: 1px solid {C_BORDER};
                border-radius: 3px;
                background: {C_CARD};
            }}
            QCheckBox::indicator:checked {{
                background: {C_ACCENT};
                border-color: {C_ACCENT};
            }}
        """

    def _btn_primary(self) -> str:
        return f"""
            QPushButton {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 {C_ACCENT2}, stop:1 {C_ACCENT});
                color: #ffffff;
                font-size: 12px;
                font-weight: bold;
                border: none;
                border-radius: 7px;
                letter-spacing: 2px;
            }}
            QPushButton:hover {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0088cc, stop:1 #00ccee);
            }}
            QPushButton:pressed {{ background: {C_ACCENT2}; }}
            QPushButton:disabled {{
                background: {C_DISABLED};
                color: {C_MUTED};
            }}
        """

    def _btn_success(self) -> str:
        return f"""
            QPushButton {{
                background: rgba(68, 204, 136, 0.1);
                color: {C_SUCCESS};
                font-size: 11px;
                border: 1px solid rgba(68, 204, 136, 0.3);
                border-radius: 7px;
            }}
            QPushButton:hover {{
                background: rgba(68, 204, 136, 0.18);
                border-color: {C_SUCCESS};
            }}
            QPushButton:disabled {{
                background: {C_DISABLED};
                color: {C_MUTED};
                border-color: {C_BORDER};
            }}
        """

    def _rb_style(self) -> str:
        return f"""
            QRadioButton {{
                color: {C_TEXT};
                font-size: 10px;
                spacing: 5px;
            }}
            QRadioButton::indicator {{
                width: 12px; height: 12px;
                border: 1px solid {C_BORDER};
                border-radius: 6px;
                background: {C_CARD};
            }}
            QRadioButton::indicator:checked {{
                background: {C_ACCENT};
                border-color: {C_ACCENT};
            }}
            QRadioButton::indicator:hover {{
                border-color: {C_ACCENT};
            }}
        """

    def _progressbar_style(self) -> str:
        return f"""
            QProgressBar {{
                background: {C_CARD};
                border: 1px solid {C_BORDER};
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 {C_ACCENT2}, stop:1 {C_ACCENT});
                border-radius: 2px;
            }}
        """
