"""
VOLUME_X - Mesh Exporter
지원 포맷: STL (binary), OBJ, PLY, VTK, STEP (AP214 ADVANCED_BREP)
"""

import os
import datetime
import numpy as np
import pyvista as pv


def export_mesh(mesh: pv.PolyData, filepath: str) -> str:
    """
    메쉬를 파일로 저장.

    Args:
        mesh:      pyvista PolyData
        filepath:  저장 경로 (확장자로 포맷 결정)

    Returns:
        실제 저장된 경로
    """
    ext = os.path.splitext(filepath)[1].lower()

    if ext == "":
        filepath += ".stl"
        ext = ".stl"

    parent = os.path.dirname(filepath)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if ext == ".stl":
        mesh.save(filepath, binary=True)

    elif ext == ".obj":
        mesh.save(filepath)

    elif ext == ".ply":
        mesh.save(filepath, binary=True)

    elif ext in [".vtk", ".vtp"]:
        mesh.save(filepath)

    elif ext in [".step", ".stp"]:
        _export_step_brep(mesh, filepath)

    else:
        filepath = filepath + ".stl"
        mesh.save(filepath, binary=True)

    return filepath


# ─────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────

def _extract_tris(mesh: pv.PolyData) -> np.ndarray:
    """삼각형 셀만 추출 (혼합 셀 타입 안전 처리, 0-indexed)"""
    mesh = mesh.copy()
    mesh.clear_data()          # ← 잔여 Normals 배열 제거 (InvalidMeshWarning 방지)
    mesh = mesh.triangulate()
    try:
        cd = mesh.cells_dict
        if pv.CellType.TRIANGLE in cd:
            return np.asarray(cd[pv.CellType.TRIANGLE], dtype=np.int64)
    except Exception:
        pass
    arr = np.array(mesh.faces, dtype=np.int64)
    starts, sizes = [], []
    i = 0
    while i < len(arr):
        n = int(arr[i]); starts.append(i); sizes.append(n); i += n + 1
    sz = np.array(sizes, dtype=np.int64)
    st = np.array(starts, dtype=np.int64)
    ts = st[sz == 3]
    if len(ts) == 0:
        return np.zeros((0, 3), dtype=np.int64)
    return np.column_stack([arr[ts+1], arr[ts+2], arr[ts+3]])


# ─────────────────────────────────────────────────────────────
# STEP AP214 Faceted B-rep Writer
# ─────────────────────────────────────────────────────────────
# Rhino / SolidWorks / CATIA / Fusion 360 등 모든 CAD SW 호환
# 각 삼각형 → ADVANCED_FACE (PLANE 표면 + EDGE_LOOP)
# 정점 / 모서리 공유로 파일 크기 최소화
#
# Entity ID 동적 할당 (충돌 방지):
#   #1–#20      : product 계층 + 기하 컨텍스트
#   CP_BASE+    : CARTESIAN_POINT × n_pts
#   VP_BASE+    : VERTEX_POINT    × n_pts
#   ED_BASE+    : DIR/VEC/LINE/EDGE_CURVE × n_edges × 4
#   FA_BASE+    : plane(5)+OE(3)+EL+FOB+AF × n_tri × 11
#   SH_ID       : CLOSED_SHELL
#   SOL_ID      : MANIFOLD_SOLID_BREP
# ─────────────────────────────────────────────────────────────

def _export_step_brep(mesh: pv.PolyData, filepath: str) -> None:
    """
    STEP AP214 ADVANCED_BREP 포맷으로 내보내기.
    모든 CAD 소프트웨어(Rhino, SolidWorks, CATIA, Fusion 360)에서 읽힘.
    """
    # ── 메쉬 정리 (잔여 데이터 제거 후 삼각형화) ─────────────
    mesh = mesh.copy()
    mesh.clear_data()
    mesh = mesh.triangulate()

    points = np.array(mesh.points, dtype=np.float64)
    faces  = _extract_tris(mesh)

    if len(faces) == 0:
        raise ValueError("내보낼 삼각형이 없습니다.")

    n_pts = len(points)
    n_tri = len(faces)

    # ── 면 법선 & 중심 계산 ────────────────────────────────────
    v0 = points[faces[:, 0]]
    v1 = points[faces[:, 1]]
    v2 = points[faces[:, 2]]
    normals = np.cross(v1 - v0, v2 - v0)
    lens    = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.where(lens < 1e-12, 1.0, lens)
    centers = (v0 + v1 + v2) / 3.0

    # 법선 바깥 방향 보정 (메쉬 중심 기준)
    mesh_center = points.mean(axis=0)
    flip = np.einsum('fi,fi->f', normals, centers - mesh_center) < 0
    normals[flip] *= -1

    # 각 면의 참조 방향 (법선과 수직인 임의 벡터)
    ref_dirs = np.zeros_like(normals)
    for i, n in enumerate(normals):
        ax = np.array([1., 0., 0.]) if abs(n[0]) < 0.9 else np.array([0., 1., 0.])
        r  = np.cross(n, ax)
        r /= max(np.linalg.norm(r), 1e-12)
        ref_dirs[i] = r

    # ── 고유 모서리 테이블 (정점 공유) ────────────────────────
    edge_map   = {}    # (va,vb) min-first → edge_idx
    edge_list  = []    # [(va, vb)]
    face_edges = []    # [(edge_idx, orient) × 3] per face

    for fi in range(n_tri):
        fe = []
        for k in range(3):
            va = int(faces[fi, k])
            vb = int(faces[fi, (k + 1) % 3])
            key = (min(va, vb), max(va, vb))
            if key not in edge_map:
                edge_map[key] = len(edge_list)
                edge_list.append(key)
            eid = edge_map[key]
            fe.append((eid, '.T.' if va < vb else '.F.'))
        face_edges.append(fe)

    n_edges = len(edge_list)

    # ── 동적 Entity ID 할당 (절대 충돌 없음) ─────────────────
    # #1–#20  : 제품 계층 + 기하 컨텍스트 (고정)
    CP_BASE = 1000                            # CARTESIAN_POINT 시작
    VP_BASE = CP_BASE + n_pts                 # VERTEX_POINT 시작
    ED_BASE = VP_BASE + n_pts                 # 모서리 엔티티 시작 (×4 per edge)
    FA_BASE = ED_BASE + n_edges * 4           # 면 엔티티 시작 (×11 per tri)
    SH_ID   = FA_BASE + n_tri * 11            # CLOSED_SHELL
    SOL_ID  = SH_ID + 1                       # MANIFOLD_SOLID_BREP

    now      = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    basename = os.path.basename(filepath)

    with open(filepath, "w", encoding="utf-8", newline="\n") as f:

        # ── HEADER ───────────────────────────────────────────
        f.write("ISO-10303-21;\n")
        f.write("HEADER;\n")
        f.write(f"FILE_DESCRIPTION(('VOLUME_X Offset Mesh'),'2;1');\n")
        f.write(f"FILE_NAME('{basename}','{now}',"
                f"('AXD Lab'),('VOLUME_X v1.0'),'','VOLUME_X 1.0','');\n")
        f.write("FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 3 1 1 1 }'));\n")
        f.write("ENDSEC;\n")
        f.write("DATA;\n")

        # ── 제품 계층 ─────────────────────────────────────────
        f.write("#1 = PRODUCT('Offset Volume','Offset Volume','',(#2));\n")
        f.write("#2 = PRODUCT_CONTEXT('',#3,'mechanical');\n")
        f.write("#3 = APPLICATION_CONTEXT("
                "'core data for automotive mechanical design processes');\n")
        f.write("#4 = PRODUCT_DEFINITION_FORMATION('','',#1);\n")
        f.write("#5 = PRODUCT_DEFINITION('design','',#4,#6);\n")
        f.write("#6 = PRODUCT_DEFINITION_CONTEXT('part definition',#3,'design');\n")
        f.write("#7 = PRODUCT_DEFINITION_SHAPE('','',#5);\n")
        f.write(f"#8 = SHAPE_DEFINITION_REPRESENTATION(#7,#20);\n")
        f.write("#9 = PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#1));\n")

        # ── 기하 컨텍스트 (mm) ───────────────────────────────
        f.write("#10 = (GEOMETRIC_REPRESENTATION_CONTEXT(3)"
                " GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#11))"
                " GLOBAL_UNIT_ASSIGNED_CONTEXT((#12,#13,#14))"
                " REPRESENTATION_CONTEXT('Ctx','3D'));\n")
        f.write("#11 = UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),"
                "#12,'distance_accuracy_value','confusion accuracy');\n")
        f.write("#12 = (LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));\n")
        f.write("#13 = (NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.));\n")
        f.write("#14 = (NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT());\n")

        # ── B-rep 최상위 참조 ─────────────────────────────────
        f.write(f"#20 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#{SOL_ID}),#10);\n")

        # ── 정점 CARTESIAN_POINT ─────────────────────────────
        for vi, p in enumerate(points):
            f.write(f"#{CP_BASE + vi} = CARTESIAN_POINT('',({p[0]:.6f},"
                    f"{p[1]:.6f},{p[2]:.6f}));\n")

        # ── VERTEX_POINT ─────────────────────────────────────
        for vi in range(n_pts):
            f.write(f"#{VP_BASE + vi} = VERTEX_POINT('',#{CP_BASE + vi});\n")

        # ── 모서리: DIRECTION, VECTOR, LINE, EDGE_CURVE ───────
        for ei, (va, vb) in enumerate(edge_list):
            b  = ED_BASE + ei * 4
            pa, pb = points[va], points[vb]
            d  = pb - pa
            dn = np.linalg.norm(d)
            d  = d / (dn if dn > 1e-12 else 1.0)
            f.write(f"#{b}   = DIRECTION('',({d[0]:.6f},{d[1]:.6f},{d[2]:.6f}));\n")
            f.write(f"#{b+1} = VECTOR('',#{b},{dn:.6f});\n")
            f.write(f"#{b+2} = LINE('',#{CP_BASE + va},#{b+1});\n")
            f.write(f"#{b+3} = EDGE_CURVE('',#{VP_BASE + va},#{VP_BASE + vb}"
                    f",#{b+2},.T.);\n")

        # ── 면: PLANE, ORIENTED_EDGE, EDGE_LOOP, FOB, ADVANCED_FACE ─
        face_af_ids = []
        for fi in range(n_tri):
            b = FA_BASE + fi * 11
            n = normals[fi]
            c = centers[fi]
            r = ref_dirs[fi]

            # plane geometry (5 entities: b ~ b+4)
            f.write(f"#{b}   = DIRECTION('',({n[0]:.6f},{n[1]:.6f},{n[2]:.6f}));\n")
            f.write(f"#{b+1} = DIRECTION('',({r[0]:.6f},{r[1]:.6f},{r[2]:.6f}));\n")
            f.write(f"#{b+2} = CARTESIAN_POINT('',({c[0]:.6f},"
                    f"{c[1]:.6f},{c[2]:.6f}));\n")
            f.write(f"#{b+3} = AXIS2_PLACEMENT_3D('',#{b+2},#{b},#{b+1});\n")
            f.write(f"#{b+4} = PLANE('',#{b+3});\n")

            # 3 oriented edges (b+5 ~ b+7)
            oe_ids = []
            for k, (eid, orient) in enumerate(face_edges[fi]):
                ec = ED_BASE + eid * 4 + 3
                oe = b + 5 + k
                f.write(f"#{oe}  = ORIENTED_EDGE('',*,*,#{ec},{orient});\n")
                oe_ids.append(oe)

            # edge loop → face outer bound → advanced face (b+8 ~ b+10)
            f.write(f"#{b+8}  = EDGE_LOOP('',("
                    f"#{oe_ids[0]},#{oe_ids[1]},#{oe_ids[2]}));\n")
            f.write(f"#{b+9}  = FACE_OUTER_BOUND('',#{b+8},.T.);\n")
            f.write(f"#{b+10} = ADVANCED_FACE('',(#{b+9}),#{b+4},.T.);\n")
            face_af_ids.append(b + 10)

        # ── CLOSED_SHELL ─────────────────────────────────────
        face_refs = ",".join(f"#{fid}" for fid in face_af_ids)
        f.write(f"#{SH_ID} = CLOSED_SHELL('',({face_refs}));\n")

        # ── MANIFOLD_SOLID_BREP ───────────────────────────────
        f.write(f"#{SOL_ID} = MANIFOLD_SOLID_BREP('Offset Volume',#{SH_ID});\n")

        f.write("ENDSEC;\n")
        f.write("END-ISO-10303-21;\n")
