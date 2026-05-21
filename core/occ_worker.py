"""
VOLUME_X - OCC NURBS Worker
cadquery-ocp(OCP) 또는 pythonocc-core(OCC)가 설치된 Python에서 subprocess로 실행됨.

오프셋 전략 (순서대로 시도):
  1. PerformBySimple          — 가장 안정적, 대부분 형상에서 동작
  2. PerformByJoin / Arc      — 부드러운 모서리
  3. PerformByJoin / Intersection — 날카로운 모서리
  4. MakeThickSolidBySimple   — 솔리드 특화

출력 스키마: AP203 (CONFIG_CONTROL_DESIGN) — 참조 파일과 동일
"""

import argparse
import json
import sys
import os


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _import_occ():
    """OCP(cadquery-ocp) 또는 OCC(pythonocc-core) 임포트"""
    for ns in ("OCP", "OCC.Core"):
        try:
            def _imp(mod, *names):
                m = __import__(f"{ns}.{mod}", fromlist=list(names))
                return {n: getattr(m, n) for n in names}

            d = {}
            d["_ns"] = ns
            d.update(_imp("BRepOffsetAPI",
                          "BRepOffsetAPI_MakeOffsetShape",
                          "BRepOffsetAPI_MakeThickSolid"))
            d.update(_imp("STEPControl",
                          "STEPControl_Reader",
                          "STEPControl_Writer",
                          "STEPControl_AsIs"))
            d.update(_imp("IFSelect",   "IFSelect_RetDone"))
            d.update(_imp("BRepMesh",   "BRepMesh_IncrementalMesh"))
            d.update(_imp("BRep",       "BRep_Tool"))
            d.update(_imp("TopAbs",     "TopAbs_FACE", "TopAbs_REVERSED"))
            d.update(_imp("TopExp",     "TopExp_Explorer"))
            d.update(_imp("TopLoc",     "TopLoc_Location"))
            d.update(_imp("GeomAbs",    "GeomAbs_Arc", "GeomAbs_Intersection"))
            d.update(_imp("BRepOffset", "BRepOffset_Skin"))
            d.update(_imp("BRepAdaptor", "BRepAdaptor_Surface"))
            d.update(_imp("BRepBuilderAPI",
                          "BRepBuilderAPI_MakeFace",
                          "BRepBuilderAPI_Sewing"))
            # 형상 힐링 (선택적)
            try:
                d.update(_imp("ShapeFix", "ShapeFix_Shape"))
                d["_has_shapefix"] = True
            except Exception:
                d["_has_shapefix"] = False

            # Interface_Static (STEP 스키마 설정용)
            try:
                d.update(_imp("Interface", "Interface_Static"))
                d["_has_interface"] = True
            except Exception:
                d["_has_interface"] = False

            return d
        except Exception:
            continue
    return None


def _heal_shape(shape, occ):
    """형상 힐링: ShapeFix로 결함 수정 후 반환"""
    if not occ.get("_has_shapefix"):
        return shape
    try:
        fix = occ["ShapeFix_Shape"](shape)
        fix.Perform()
        return fix.Shape()
    except Exception:
        return shape


def _fix_flat_vertical_faces(result_shape, dist, occ):
    """
    PerformBySimple 후 평면 수직 면(법선이 XY평면 내)의 Z 방향 파라미터 범위를
    ±dist 만큼 확장하여 올바른 오프셋 적용.

    PerformBySimple 은 면을 각각 독립적으로 오프셋하기 때문에
    XY 방향(법선 방향)은 정확히 dist 만큼 이동하나,
    Z 방향(면 내 파라미터)의 트리밍 경계는 원본 그대로 남는다.
    이 함수는 그 경계를 ±dist 연장한 새 face 로 교체한다.
    """
    _ns = occ.get("_ns", "OCP")

    try:
        BRepAdaptor_Surface  = occ["BRepAdaptor_Surface"]
        BRepBuilderAPI_MakeFace = occ["BRepBuilderAPI_MakeFace"]
        BRepBuilderAPI_Sewing   = occ["BRepBuilderAPI_Sewing"]
        BRep_Tool        = occ["BRep_Tool"]
        TopExp_Explorer  = occ["TopExp_Explorer"]
        TopAbs_FACE      = occ["TopAbs_FACE"]
        TopLoc_Location  = occ["TopLoc_Location"]

        # TopoDS.Face_s 캐스팅
        _to_face = None
        try:
            _TopoDS = __import__(f"{_ns}.TopoDS", fromlist=["TopoDS"])
            _to_face = getattr(_TopoDS.TopoDS, "Face_s", None)
        except Exception:
            pass
        if _to_face is None:
            try:
                _topods = __import__(f"{_ns}.TopoDS", fromlist=["topods"])
                _to_face = _topods.topods.Face
            except Exception:
                pass
        if _to_face is None:
            try:
                _topods_Face = __import__("OCC.Core.TopoDS", fromlist=["topods_Face"])
                _to_face = _topods_Face.topods_Face
            except Exception:
                pass
        if _to_face is None:
            _to_face = lambda s: s

        # BRep_Tool.Surface 정적 메서드
        _surf_func = getattr(BRep_Tool, "Surface_s",
                     getattr(BRep_Tool, "Surface", None))

        GEOMABS_PLANE = 0   # GeomAbs_Plane 정수값

        new_faces = []
        exp = TopExp_Explorer(result_shape, TopAbs_FACE)
        while exp.More():
            raw = exp.Current()
            try:
                face = _to_face(raw)
            except Exception:
                face = raw

            try:
                adaptor = BRepAdaptor_Surface(face)
                surf_type = int(adaptor.GetType())

                if surf_type == GEOMABS_PLANE:
                    plane = adaptor.Plane()
                    n = plane.Axis().Direction()

                    if abs(n.Z()) < 0.01:          # 수직 평면(법선이 XY 내)
                        ax3 = plane.Position()
                        u_dir = ax3.XDirection()
                        v_dir = ax3.YDirection()
                        u_z = abs(u_dir.Z())
                        v_z = abs(v_dir.Z())

                        u1 = adaptor.FirstUParameter()
                        u2 = adaptor.LastUParameter()
                        v1 = adaptor.FirstVParameter()
                        v2 = adaptor.LastVParameter()

                        if u_z > v_z:   # u 방향이 Z 축
                            nu1, nu2 = u1 - dist, u2 + dist
                            nv1, nv2 = v1, v2
                        else:           # v 방향이 Z 축
                            nu1, nu2 = u1, u2
                            nv1, nv2 = v1 - dist, v2 + dist

                        # gp_Pln 은 이미 월드 좌표계 — BRepBuilderAPI_MakeFace에 직접 전달
                        gp_plane = plane   # adaptor.Plane() 반환값 (gp_Pln)
                        try:
                            mf = BRepBuilderAPI_MakeFace(
                                gp_plane, nu1, nu2, nv1, nv2)
                        except Exception:
                            # 일부 OCC 버전: Geom_Surface 경유
                            loc = TopLoc_Location()
                            geom_surf = _surf_func(face, loc)
                            mf = BRepBuilderAPI_MakeFace(
                                geom_surf, nu1, nu2, nv1, nv2, 1e-6)
                        if mf.IsDone():
                            new_faces.append(mf.Face())
                        else:
                            new_faces.append(face)   # 실패 시 원본 유지
                    else:
                        new_faces.append(face)       # 수평면은 그대로
                else:
                    new_faces.append(face)           # 돔 OffsetSurface 그대로
            except Exception:
                new_faces.append(face)

            exp.Next()

        if not new_faces:
            return result_shape

        # 모든 면을 봉합
        # sewing tolerance: dist * 1.5 로 설정해 돔-벽 접합부 간극을 포함
        sew = BRepBuilderAPI_Sewing(max(0.1, dist * 0.5))
        for f in new_faces:
            sew.Add(f)
        sew.Perform()
        sewn = sew.SewedShape()

        # ShapeFix
        sewn = _heal_shape(sewn, occ)
        return sewn

    except Exception as e:
        emit({"debug": f"fix_flat_vertical_faces 실패, 원본 사용: {e}"})
        return result_shape


def _try_offset(shape, distance, tolerance, occ):
    """
    4가지 전략으로 오프셋 시도.
    성공 시 (result_shape, strategy_name), 실패 시 (None, [error_list]) 반환.
    """
    errors = []

    # ── 전략 1: PerformBySimple (가장 안정적) ──────────────────
    try:
        maker = occ["BRepOffsetAPI_MakeOffsetShape"]()
        maker.PerformBySimple(shape, distance)
        if maker.IsDone():
            result = maker.Shape()
            result = _heal_shape(result, occ)
            # 평면 수직면의 Z 방향 트리밍 경계를 ±distance 확장
            result = _fix_flat_vertical_faces(result, distance, occ)
            return result, "PerformBySimple+ZFix"
        errors.append("PerformBySimple: IsDone=False")
    except Exception as e:
        errors.append(f"PerformBySimple: {e}")

    # ── 전략 2: PerformByJoin / Arc ────────────────────────────
    try:
        maker = occ["BRepOffsetAPI_MakeOffsetShape"]()
        maker.PerformByJoin(
            shape, distance, tolerance,
            occ["BRepOffset_Skin"],
            False, False,
            occ["GeomAbs_Arc"],
            True          # RemoveIntEdges
        )
        if maker.IsDone():
            result = maker.Shape()
            result = _heal_shape(result, occ)
            return result, "PerformByJoin/Arc"
        errors.append("PerformByJoin/Arc: IsDone=False")
    except Exception as e:
        errors.append(f"PerformByJoin/Arc: {e}")

    # ── 전략 3: PerformByJoin / Intersection ───────────────────
    try:
        maker = occ["BRepOffsetAPI_MakeOffsetShape"]()
        maker.PerformByJoin(
            shape, distance, tolerance,
            occ["BRepOffset_Skin"],
            False, False,
            occ["GeomAbs_Intersection"],
            True          # RemoveIntEdges
        )
        if maker.IsDone():
            result = maker.Shape()
            result = _heal_shape(result, occ)
            return result, "PerformByJoin/Intersection"
        errors.append("PerformByJoin/Intersection: IsDone=False")
    except Exception as e:
        errors.append(f"PerformByJoin/Intersection: {e}")

    # ── 전략 4: MakeThickSolidBySimple ─────────────────────────
    try:
        thick = occ["BRepOffsetAPI_MakeThickSolid"]()
        thick.MakeThickSolidBySimple(shape, distance)
        thick.Build()
        if thick.IsDone():
            result = thick.Shape()
            result = _heal_shape(result, occ)
            return result, "MakeThickSolidBySimple"
        errors.append("MakeThickSolidBySimple: IsDone=False")
    except Exception as e:
        errors.append(f"MakeThickSolidBySimple: {e}")

    return None, errors


def _set_step_write_options(occ):
    """STEP 쓰기 옵션 설정 (OCP 7.9 Interface_Static _s suffix API)"""
    if not occ.get("_has_interface"):
        return
    try:
        stat = occ["Interface_Static"]
        # OCP 7.9 uses _s suffix for static methods
        _set = getattr(stat, "SetCVal_s", None) or getattr(stat, "SetCVal", None)
        if _set:
            _set("write.step.unit", "MM")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="VOLUME_X OCC NURBS Worker")
    parser.add_argument("--input",      required=True)
    parser.add_argument("--output",     required=True)
    parser.add_argument("--distance",   type=float, required=True)
    parser.add_argument("--tolerance",  type=float, default=0.01)
    parser.add_argument("--deflection", type=float, default=0.05)
    args = parser.parse_args()

    occ = _import_occ()
    if occ is None:
        emit({"error": "OCP/OCC 임포트 실패 — cadquery-ocp 또는 pythonocc-core 설치 필요"})
        sys.exit(1)

    # ── 1. STEP 로드 ────────────────────────────────────────────
    emit({"status": "loading"})
    reader = occ["STEPControl_Reader"]()
    status = reader.ReadFile(str(args.input))
    if status != occ["IFSelect_RetDone"]:
        emit({"error": f"STEP 읽기 실패: {args.input}"})
        sys.exit(1)
    reader.TransferRoots()
    shape = reader.OneShape()

    # ── 2. NURBS 오프셋 (다중 전략) ────────────────────────────
    emit({"status": "offsetting"})
    result_shape, strategy = _try_offset(
        shape, args.distance, args.tolerance, occ
    )

    if result_shape is None:
        err_detail = "\n".join(strategy) if isinstance(strategy, list) else str(strategy)
        emit({"error": (
            f"NURBS 오프셋 실패 (거리={args.distance:.1f}mm).\n\n"
            f"시도 결과:\n{err_detail}\n\n"
            "해결 방법:\n"
            "• 오프셋 거리를 줄여보세요\n"
            "• 입력 형상이 닫힌 솔리드인지 확인하세요\n"
            "• 매우 얇은 부분이나 극단적인 곡률이 있으면 실패할 수 있습니다"
        )})
        sys.exit(1)

    # ── 3. NURBS STEP 내보내기 ──────────────────────────────────
    emit({"status": "exporting_step", "strategy": strategy})
    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # STEP 쓰기 옵션 설정
    _set_step_write_options(occ)

    writer = occ["STEPControl_Writer"]()
    writer.Transfer(result_shape, occ["STEPControl_AsIs"])
    wr_status = writer.Write(str(args.output))
    if wr_status != occ["IFSelect_RetDone"]:
        emit({"error": f"STEP 쓰기 실패: {args.output}"})
        sys.exit(1)

    # ── 4. 뷰어용 메쉬 생성 ────────────────────────────────────
    emit({"status": "meshing"})
    import numpy as np

    mesh_gen = occ["BRepMesh_IncrementalMesh"](
        result_shape, args.deflection, False, 0.5, True
    )
    mesh_gen.Perform()

    # OCP 버전에 따라 메서드명이 다름 (7.9+: Triangulation_s, 이전: Triangulation)
    _brt = occ["BRep_Tool"]
    _tri_func = getattr(_brt, "Triangulation_s",
                 getattr(_brt, "Triangulation", None))

    # TopoDS_Shape → TopoDS_Face 캐스팅 함수 (OCP 버전별 대응)
    _to_face = None
    try:
        from OCP.TopoDS import TopoDS
        _to_face = TopoDS.Face_s
    except Exception:
        pass

    if _to_face is None:
        try:
            from OCP.TopoDS import topods
            _to_face = topods.Face
        except Exception:
            pass

    if _to_face is None:
        try:
            from OCC.Core.TopoDS import topods_Face
            _to_face = topods_Face
        except Exception:
            pass

    if _to_face is None:
        _to_face = lambda s: s  # 캐스팅 불가 시 그대로 사용

    all_pts  = []
    all_tris = []
    v_offset = 0

    exp = occ["TopExp_Explorer"](result_shape, occ["TopAbs_FACE"])
    while exp.More():
        raw_face = exp.Current()
        try:
            face = _to_face(raw_face)
        except Exception:
            face = raw_face

        loc  = occ["TopLoc_Location"]()
        tri  = _tri_func(face, loc)

        if tri is not None and tri.NbTriangles() > 0:
            n_nodes = tri.NbNodes()
            for i in range(1, n_nodes + 1):
                p = tri.Node(i)
                all_pts.append([p.X(), p.Y(), p.Z()])

            reversed_face = (face.Orientation() == occ["TopAbs_REVERSED"])
            for i in range(1, tri.NbTriangles() + 1):
                t = tri.Triangle(i)
                n1, n2, n3 = t.Get()
                if reversed_face:
                    all_tris.append([v_offset + n1 - 1,
                                     v_offset + n3 - 1,
                                     v_offset + n2 - 1])
                else:
                    all_tris.append([v_offset + n1 - 1,
                                     v_offset + n2 - 1,
                                     v_offset + n3 - 1])
            v_offset += n_nodes
        exp.Next()

    if not all_pts:
        emit({"error": "메쉬 변환 실패: 삼각형이 생성되지 않았습니다. 형상을 확인하세요."})
        sys.exit(1)

    # ── 5. 완료 ────────────────────────────────────────────────
    emit({
        "status":   "done",
        "strategy": strategy,
        "schema":   "AP214",   # OCC default — Rhino/SW/CATIA 완전 호환
        "mesh": {
            "points": all_pts,
            "faces":  all_tris,
        }
    })


if __name__ == "__main__":
    main()
