"""
VOLUME_X - 3D File Loader
Supports: STL, OBJ, PLY, FBX, STEP/STP, IGES
"""

import os
import numpy as np
import pyvista as pv


SUPPORTED_FORMATS = {
    "직접 로드 (pyvista)": [".stl", ".obj", ".ply", ".vtk", ".vtp"],
    "trimesh 경유":        [".fbx", ".dae", ".3ds", ".glb", ".gltf"],
    "CAD (cascadio)":     [".step", ".stp", ".iges", ".igs"],
}


def load_mesh_file(filepath: str) -> pv.PolyData:
    """
    범용 3D 파일 로더. 확장자에 따라 최적 라이브러리로 로드.

    Returns:
        pyvista.PolyData  (표면 메쉬 + 법선 포함)
    Raises:
        ValueError / ImportError: 지원하지 않거나 로드 실패 시
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {filepath}")

    ext = os.path.splitext(filepath)[1].lower()

    # ── 1. pyvista 직접 로드 ──────────────────────────────────
    if ext in [".stl", ".obj", ".ply", ".vtk", ".vtp"]:
        return _load_pyvista(filepath)

    # ── 2. CAD 형식 ──────────────────────────────────────────
    if ext in [".step", ".stp", ".iges", ".igs"]:
        return _load_step(filepath)

    # ── 3. trimesh 경유 (FBX, DAE, GLB …) ────────────────────
    return _load_via_trimesh(filepath)


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────

def _load_pyvista(filepath: str) -> pv.PolyData:
    mesh = pv.read(filepath)
    mesh = mesh.extract_surface(algorithm="dataset_surface")
    mesh = _clean_and_normal(mesh)
    return mesh


def _load_via_trimesh(filepath: str) -> pv.PolyData:
    try:
        import trimesh
    except ImportError:
        raise ImportError("trimesh가 필요합니다: pip install trimesh[all]")

    loaded = trimesh.load(filepath, force="mesh")

    if isinstance(loaded, trimesh.Scene):
        meshes = [m for m in loaded.geometry.values()
                  if isinstance(m, trimesh.Trimesh)]
        if not meshes:
            raise ValueError("씬 안에 메쉬가 없습니다.")
        combined = trimesh.util.concatenate(meshes)
    elif isinstance(loaded, trimesh.Trimesh):
        combined = loaded
    else:
        raise ValueError(f"지원하지 않는 오브젝트 타입: {type(loaded)}")

    return _trimesh_to_pyvista(combined)


def _load_step(filepath: str) -> pv.PolyData:
    """STEP / IGES → pyvista
    우선순위: cascadio → trimesh → 오류
    """
    # ── 1. cascadio (이미 설치됨, 가장 정확) ─────────────────
    try:
        return _load_step_cascadio(filepath)
    except Exception as e:
        cascadio_err = str(e)

    # ── 2. trimesh fallback ───────────────────────────────────
    try:
        return _load_via_trimesh(filepath)
    except Exception:
        pass

    # ── 3. pythonocc fallback ─────────────────────────────────
    try:
        return _load_step_occ(filepath)
    except ImportError:
        pass

    raise ValueError(
        f"STEP/IGES 파일을 로드할 수 없습니다.\n"
        f"cascadio 오류: {cascadio_err}\n\n"
        f"파일이 손상됐거나 지원하지 않는 형식일 수 있습니다."
    )


def _load_step_cascadio(filepath: str) -> pv.PolyData:
    """cascadio로 STEP → OBJ 변환 후 로드 (pythonocc 불필요)"""
    import tempfile, cascadio

    ext = os.path.splitext(filepath)[1].lower()

    with tempfile.TemporaryDirectory() as tmpdir:
        out_obj = os.path.join(tmpdir, "converted.obj")

        if ext in [".step", ".stp"]:
            ret = cascadio.step_to_obj(
                filepath, out_obj,
                tol_linear=0.05,   # 0.05mm 정밀도
                tol_angular=0.3,
                use_colors=False,
            )
        else:
            # IGES 는 step_to_obj 미지원 → GLB 경유
            out_glb = os.path.join(tmpdir, "converted.glb")
            ret = cascadio.step_to_glb(filepath, out_glb, tol_linear=0.05)
            if ret != 0:
                raise ValueError(f"cascadio 변환 실패 (code {ret})")
            return _load_via_trimesh(out_glb)

        if ret != 0:
            raise ValueError(f"cascadio STEP 변환 실패 (code {ret})")

        if not os.path.isfile(out_obj):
            raise ValueError("cascadio: OBJ 출력 파일이 생성되지 않았습니다.")

        mesh = pv.read(out_obj)

    # OBJ 변환 후 불필요한 cell 배열 제거 (GroupIds 등 길이 불일치 방지)
    mesh.clear_data()
    return _clean_and_normal(mesh.extract_surface(algorithm="dataset_surface"))


def _load_step_occ(filepath: str) -> pv.PolyData:
    """pythonocc(OpenCASCADE) fallback"""
    import tempfile

    ext = os.path.splitext(filepath)[1].lower()

    if ext in [".step", ".stp"]:
        from OCC.Core.STEPControl import STEPControl_Reader
        from OCC.Core.IFSelect import IFSelect_RetDone
        reader = STEPControl_Reader()
        if reader.ReadFile(filepath) != IFSelect_RetDone:
            raise ValueError("STEP 파일 읽기 실패")
        reader.TransferRoots()
        shape = reader.OneShape()
    else:
        from OCC.Core.IGESControl import IGESControl_Reader
        from OCC.Core.IFSelect import IFSelect_RetDone
        reader = IGESControl_Reader()
        if reader.ReadFile(filepath) != IFSelect_RetDone:
            raise ValueError("IGES 파일 읽기 실패")
        reader.TransferRoots()
        shape = reader.OneShape()

    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.StlAPI import StlAPI_Writer

    BRepMesh_IncrementalMesh(shape, 0.05, False, 0.3).Perform()
    with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as f:
        tmp = f.name
    try:
        StlAPI_Writer().Write(shape, tmp)
        mesh = pv.read(tmp)
    finally:
        os.unlink(tmp)

    return _clean_and_normal(mesh.extract_surface())


def _trimesh_to_pyvista(tm_mesh) -> pv.PolyData:
    vertices = np.asarray(tm_mesh.vertices, dtype=np.float64)
    faces    = np.asarray(tm_mesh.faces,    dtype=np.int64)

    pv_faces = np.hstack([
        np.full((len(faces), 1), 3, dtype=np.int64),
        faces
    ]).flatten()

    mesh = pv.PolyData(vertices, pv_faces)
    return _clean_and_normal(mesh)


def _clean_and_normal(mesh: pv.PolyData) -> pv.PolyData:
    # 불필요한 배열 제거 후 정리 (InvalidMeshWarning 방지)
    mesh.clear_data()
    mesh = mesh.clean(tolerance=1e-6)
    mesh = mesh.triangulate()
    mesh = mesh.compute_normals(
        cell_normals=True,
        point_normals=True,
        consistent_normals=True,
        auto_orient_normals=True,
        non_manifold_traversal=False,
        split_vertices=False,
    )
    return mesh
