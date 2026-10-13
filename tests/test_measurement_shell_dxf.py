from geometry.measurement_shell_dxf import DxfImportError, import_dxf


def _dxf(entities: str) -> str:
    return f"""0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n4\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n{entities}0\nENDSEC\n0\nEOF\n"""


def _line(layer, x1, y1, x2, y2):
    return f"0\nLINE\n8\n{layer}\n10\n{x1}\n20\n{y1}\n11\n{x2}\n21\n{y2}\n"


def _point(layer, x, y):
    return f"0\nPOINT\n8\n{layer}\n10\n{x}\n20\n{y}\n"


def test_imports_closed_region_and_sensor_tip():
    entities = "".join([
        _line("MS_MATERIAL_BOUNDARY_01", 0, 0, 10, 0),
        _line("MS_MATERIAL_BOUNDARY_01", 10, 0, 10, 5),
        _line("MS_MATERIAL_BOUNDARY_01", 10, 5, 0, 5),
        _line("MS_MATERIAL_BOUNDARY_01", 0, 5, 0, 0),
        _line("MS_AXIS", -5, 0, 15, 0),
        _point("MS_SENSOR_TIP", 10, 2.5),
    ])
    result = import_dxf(_dxf(entities), {"regions": [{"layer": "MS_MATERIAL_BOUNDARY_01", "material": "silicone_A30"}]})
    assert result["version"] == 2
    assert len(result["geometry"]["regions"]) == 1
    assert len(result["geometry"]["regions"][0]["boundary"]) == 4
    assert result["metadata"]["sensor_tip_point"]
    assert any(entity["construction"] for entity in result["geometry"]["elements"])


def test_arc_endpoints_are_used_for_closed_contour():
    entities = "".join([
        "0\nARC\n8\nMS_MATERIAL_BOUNDARY_01\n10\n0\n20\n0\n40\n10\n50\n0\n51\n180\n",
        _line("MS_MATERIAL_BOUNDARY_01", -10, 0, -10, 5),
        _line("MS_MATERIAL_BOUNDARY_01", -10, 5, 10, 5),
        _line("MS_MATERIAL_BOUNDARY_01", 10, 5, 10, 0),
        _point("MS_SENSOR_TIP", 10, 0),
    ])
    result = import_dxf(_dxf(entities))
    assert len(result["geometry"]["regions"][0]["boundary"]) == 4


def test_open_profile_is_rejected():
    entities = "".join([
        _line("MS_OUTER_PROFILE", 0, 0, 10, 0),
        _line("MS_OUTER_PROFILE", 10, 0, 10, 5),
        _point("MS_SENSOR_TIP", 10, 2.5),
    ])
    try:
        import_dxf(_dxf(entities))
    except DxfImportError as error:
        assert "geschlossen" in str(error)
    else:
        raise AssertionError("Offene DXF-Kontur wurde akzeptiert.")
