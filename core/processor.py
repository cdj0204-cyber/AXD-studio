"""
VOLUME_X - Mesh Offset & Smoothing Engine  v2.0

알고리즘: Face Normal Plane Intersection (면법선 평면 교차법)
────────────────────────────────────────────────────────────
기존 vertex-normal 방식의 문제:
  - 모서리 꼭짓점의 법선 = 인접 면 법선의 평균 → 대각선 이동
  - 평평한 면의 모서리가 무너지거나 뒤집히는 현상 발생

새 알고리즘:
  - 각 꼭짓점에 대해 인접한 모든 면의 "offset된 평면" 방정식을 세움
  - 최소자승법으로 교차점 계산 → 평면·모서리·코너 모두 정확히 처리
  - 완전 numpy 벡터화 → 루프 없음, CPU 최소 사용
"""

import numpy as np
import pyvista as pv


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────

def _extract_triangles(mesh: pv.PolyData) -> np.ndarray:
    """
    pyvista 메쉬에서 삼각형 셀만 안전하게 추출.
    triangulate() 이후에도 간혹 비삼각형 셀이 잔존하는 경우를 처리.
    Returns: np.ndarray shape (F, 3) — 삼각형 꼭짓점 인덱스
    """
    # 방법 1: cells_dict 사용 (pyvista 권장 API)
    try:
        import pyvista as pv
        cd = mesh.cells_dict
        tri_key = pv.CellType.TRIANGLE
        if tri_key in cd:
            return np.asarray(cd[tri_key], dtype=np.int64)
    except Exception:
        pass

    # 방법 2: faces 배열 직접 파싱 (완전 벡터화)
    faces_flat = np.array(mesh.faces, dtype=np.int64)
    if faces_flat.size == 0:
        return np.zeros((0, 3), dtype=np.int64)

    # 각 셀 크기를 추적해서 삼각형(n=3)만 선택
    # faces_flat = [n0, v0, v1, ..., n1, v0, v1, ...]
    # 셀 시작 인덱스를 스캔 (Python loop 최소화)
    arr = faces_flat
    starts, sizes = [], []
    i = 0
    while i < len(arr):
        n = int(arr[i])
        starts.append(i)
        sizes.append(n)
        i += n + 1

    sizes  = np.array(sizes,  dtype=np.int64)
    starts = np.array(starts, dtype=np.int64)

    tri_mask   = (sizes == 3)
    tri_starts = starts[tri_mask]

    if len(tri_starts) == 0:
        return np.zeros((0, 3), dtype=np.int64)

    return np.column_stack([
        arr[tri_starts + 1],
        arr[tri_starts + 2],
        arr[tri_starts + 3],
    ])


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def offset_mesh(mesh: pv.PolyData, distance_mm: float) -> pv.PolyData:
    """
    메쉬 표면을 distance_mm 만큼 균일하게 오프셋.

    면법선 평면 교차법(FNPI) 사용:
      · 평평한 면   → face normal 방향으로 정확히 distance 이동 ✓
      · 볼록 모서리 → 자동으로 miter(경사) 처리 ✓
      · 코너        → 세 평면의 교차점으로 정확히 계산 ✓

    Args:
        mesh:        pyvista PolyData
        distance_mm: 오프셋 거리 (mm)
    Returns:
        오프셋된 pyvista PolyData
    """
    if distance_mm <= 0:
        raise ValueError("오프셋 거리는 0보다 커야 합니다.")

    # 잔여 배열 제거 후 삼각형 통일
    mesh = mesh.copy()
    mesh.clear_data()
    mesh = mesh.triangulate()
    mesh = mesh.clean(tolerance=1e-6)

    points = np.array(mesh.points, dtype=np.float64)   # (V, 3)
    V = len(points)

    # 삼각형 셀만 안전하게 추출 (혼합 셀 타입 완전 처리)
    faces = _extract_triangles(mesh)

    # 면 법선을 외적(cross product)으로 직접 계산 → 개수 불일치 원천 차단
    v0 = points[faces[:, 0]]
    v1 = points[faces[:, 1]]
    v2 = points[faces[:, 2]]
    face_normals = np.cross(v1 - v0, v2 - v0)          # (F, 3)

    # 단위 벡터 정규화 (영벡터 보호)
    norms = np.linalg.norm(face_normals, axis=1, keepdims=True)
    valid = (norms > 1e-12).flatten()
    norms = np.where(norms < 1e-12, 1.0, norms)
    face_normals = face_normals / norms

    # 법선 방향 일관성: 외부를 향하도록 (mesh center 기준 뒤집기)
    center = points.mean(axis=0)
    face_centers = (v0 + v1 + v2) / 3.0
    outward = face_centers - center
    flip_mask = np.einsum('fi,fi->f', face_normals, outward) < 0
    face_normals[flip_mask] *= -1
    # 유효하지 않은 면(zero-area) 제외
    face_normals[~valid] = 0

    # ── FNPI 핵심 계산 ──────────────────────────────────────
    #
    # 각 꼭짓점 v 에 대해 인접 면 i 의 offset 평면:
    #   n_i · x = n_i · p_v + d
    #
    # 정규 방정식(normal equations)으로 최소자승 교차점 계산:
    #   (Σ n_i n_i^T) x = Σ n_i (n_i · p_v + d)
    #      AtA  ·  x  =         Atb
    #
    # [완전 벡터화 구현]

    # outer product n_i ⊗ n_i → (F, 3, 3)
    nnt = face_normals[:, :, None] * face_normals[:, None, :]

    # n_i · p_v  for each (face, vertex_in_face) → (F, 3)
    n_dot_p = np.einsum('fi,fvi->fv', face_normals, points[faces])

    # b = n_i · p_v + d → (F, 3)
    b = n_dot_p + distance_mm

    # rhs contribution: n_i * b  → (F, 3_verts, 3_coords)
    rhs = face_normals[:, None, :] * b[:, :, None]

    # 꼭짓점별 누적
    AtA = np.zeros((V, 3, 3))
    Atb = np.zeros((V, 3))

    for k in range(3):                          # 삼각형 꼭짓점 3개
        vid = faces[:, k]                       # (F,)
        np.add.at(AtA, vid, nnt)                # AtA[v] += n_i n_i^T
        np.add.at(Atb, vid, rhs[:, k, :])      # Atb[v] += n_i * b

    # 수치 안정성 정규화 (singular 방지)
    AtA += np.eye(3) * 1e-8

    # 전체 꼭짓점 선형방정식 일괄 풀기
    # numpy batched solve: AtA (V,3,3) @ x = Atb (V,3,1) → (V,3)
    new_points = np.linalg.solve(AtA, Atb[:, :, None]).squeeze(-1)

    # 결과 메쉬 생성 및 정리
    result = mesh.copy()
    result.points = new_points
    result = result.clean(tolerance=1e-5)
    result = result.compute_normals(
        consistent_normals=True,
        auto_orient_normals=True,
    )

    return result


def smooth_mesh(
    mesh: pv.PolyData,
    iterations: int = 20,
    pass_band: float = 0.1,
) -> pv.PolyData:
    """
    Taubin 스무딩 (볼륨 보존형).

    ※ 기계 부품의 sharp edge 보호를 위해 기본값을 낮게 설정.
       iterations 를 0 으로 하면 스무딩 없이 오프셋만 적용.

    Args:
        mesh:       pyvista PolyData
        iterations: 반복 횟수 (0 = 스무딩 없음)
        pass_band:  주파수 컷오프 (낮을수록 더 부드러움, 0.01~0.5)
    """
    if iterations == 0:
        return mesh

    smoothed = mesh.smooth_taubin(
        n_iter=iterations,
        pass_band=pass_band,
        boundary_smoothing=True,
        normalize_coordinates=True,
        non_manifold_smoothing=True,
    )

    smoothed = smoothed.compute_normals(
        consistent_normals=True,
        auto_orient_normals=True,
    )

    return smoothed


def get_mesh_info(mesh: pv.PolyData) -> dict:
    """메쉬 기본 정보 반환"""
    bounds = mesh.bounds
    return {
        "n_points": mesh.n_points,
        "n_faces":  mesh.n_cells,
        "dim_x":    bounds[1] - bounds[0],
        "dim_y":    bounds[3] - bounds[2],
        "dim_z":    bounds[5] - bounds[4],
        "center":   mesh.center,
    }
