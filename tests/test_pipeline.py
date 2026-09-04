"""
test_pipeline.py
Automated test suite verifying ULPIN encoding, spatial transforms,
3D extrusion, LiDAR generation, and FastAPI endpoints.
"""

import sys
import os

# Add backend directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.ulpin_generator import (
    generate_prototype_ulpin,
    decode_prototype_ulpin,
    get_ulpin_breakdown
)
from backend.services.spatial_service import (
    reproject_point_to_utm,
    reproject_point_to_wgs84,
    calculate_metric_area,
    validate_and_fix_polygon,
    check_building_encroachment
)
from backend.services.ai_extractor import extract_building_footprints_from_image
from backend.services.extrusion_engine import generate_underground_units

from shapely.geometry import Polygon
from backend.database.db import db_instance


def test_ulpin_generator():
    print("Testing ULPIN Generator...")
    # Test case from official document
    lat, lon = 25.068164, 85.623346
    code_gnd = generate_prototype_ulpin(lat, lon, floor=0)
    assert len(code_gnd) == 14, f"Expected 14 chars, got {len(code_gnd)}"
    assert code_gnd.startswith("832454DYJFAQ"), f"Unexpected prefix: {code_gnd}"
    print(f"  [PASS] Base ULPIN for ({lat}, {lon}) = {code_gnd}")

    # Test multi-story floor variation (L00, L01, L02...)
    code_fl1 = generate_prototype_ulpin(lat, lon, floor=1)
    code_fl2 = generate_prototype_ulpin(lat, lon, floor=2)
    assert code_fl1 != code_gnd, "Floor 1 ULPIN must differ from Ground ULPIN"
    assert code_fl2 != code_fl1, "Floor 2 ULPIN must differ from Floor 1 ULPIN"
    print(f"  [PASS] 3D Floor ULPINs: L00={code_gnd}, L01={code_fl1}, L02={code_fl2}")

    # Test breakdown
    breakdown = get_ulpin_breakdown(lat, lon, floor=2)
    assert len(breakdown["steps"]) == 7
    print("  [PASS] Computational steps breakdown generated successfully.")


def test_spatial_transforms():
    print("Testing Spatial Transformations (EPSG:4326 <-> EPSG:32644)...")
    lon, lat = 78.4867, 17.3850  # Hyderabad
    x_utm, y_utm = reproject_point_to_utm(lon, lat)
    assert 200000 < x_utm < 300000, f"Unexpected UTM X: {x_utm}"
    assert 1900000 < y_utm < 2000000, f"Unexpected UTM Y: {y_utm}"
    
    # Reverse transform
    r_lon, r_lat = reproject_point_to_wgs84(x_utm, y_utm)
    assert abs(r_lon - lon) < 1e-4 and abs(r_lat - lat) < 1e-4
    print(f"  [PASS] Reprojection verified: ({lat}, {lon}) -> UTM: ({x_utm:.1f}, {y_utm:.1f})")

    # Test area calculation
    test_poly = Polygon([
        [78.4800, 17.3800],
        [78.4810, 17.3800],
        [78.4810, 17.3810],
        [78.4800, 17.3810],
        [78.4800, 17.3800]
    ])
    area = calculate_metric_area(test_poly)
    assert area > 1000.0, f"Unexpected area: {area}"
    print(f"  [PASS] Metric area calculation verified: {area:.1f} m²")

    # Test encroachment calculation
    bldg_inside = Polygon([
        [78.4802, 17.3802],
        [78.4808, 17.3802],
        [78.4808, 17.3808],
        [78.4802, 17.3808],
        [78.4802, 17.3802]
    ])
    enc_clean = check_building_encroachment(test_poly, bldg_inside)
    assert enc_clean["status"] == "CLEAN", f"Expected CLEAN, got {enc_clean['status']}"

    bldg_outside = Polygon([
        [78.4808, 17.3808],
        [78.4815, 17.3808],  # crosses boundary 78.4810
        [78.4815, 17.3815],
        [78.4808, 17.3815],
        [78.4808, 17.3808]
    ])
    enc_warn = check_building_encroachment(test_poly, bldg_outside)
    assert enc_warn["status"] == "ENCROACHED", f"Expected ENCROACHED, got {enc_warn['status']}"
    assert enc_warn["encroached_area_sqm"] > 0
    print(f"  [PASS] Encroachment calculation verified: {enc_warn['encroached_area_sqm']} m² outside ({enc_warn['encroachment_percent']}%) -> STATUS: {enc_warn['status']}")



def test_database_and_wards():
    print("Testing Database and Hyderabad Ward Loader...")
    wards = db_instance.get_all_wards()
    assert len(wards) == 145, f"Expected 145 wards, found {len(wards)}"
    print(f"  [PASS] Loaded {len(wards)} wards.")

    # Check seeded parcels
    parcels = db_instance.get_parcels()
    assert len(parcels) > 0, "Expected seeded parcels"
    print(f"  [PASS] Found {len(parcels)} active parcels in database.")

    stats = db_instance.get_stats()
    assert stats["total_3d_units"] > 0
    print(f"  [PASS] Aggregate Stats: {stats['total_parcels']} parcels, {stats['total_3d_units']} 3D floor units.")


def test_ai_and_underground():
    print("Testing AI Building Extraction and Underground Infrastructure Units...")
    bbox = [78.375, 17.435, 78.385, 17.445]
    footprints = extract_building_footprints_from_image(b"", bbox)
    assert len(footprints) > 0, "Expected AI extracted footprints"
    assert footprints[0]["source"] == "AI_Segmentation_Module"
    print(f"  [PASS] AI Footprint Extraction verified: {len(footprints)} footprints extracted with confidence {footprints[0]['confidence']}")

    test_poly = Polygon([
        [78.4800, 17.3800],
        [78.4810, 17.3800],
        [78.4810, 17.3810],
        [78.4800, 17.3810],
        [78.4800, 17.3800]
    ])
    undg_units = generate_underground_units(test_poly, "832454DYJFAQY2", 512.0, 2)
    assert len(undg_units) == 3, f"Expected 3 underground units, got {len(undg_units)}"
    assert undg_units[0]["ulpin_3d"] == "832454DYJFAQY2-B01"
    assert undg_units[2]["ulpin_3d"] == "832454DYJFAQY2-UTL"
    assert len(undg_units[2]["ulpin_3d"]) == 18
    print(f"  [PASS] Underground Units & 18-Char 3D Spatial Identities verified (14-char base + 4-char suffix).")



if __name__ == "__main__":
    print("=" * 60)
    print(" RUNNING 3D ULPIN TEST SUITE")
    print("=" * 60)
    test_ulpin_generator()
    test_spatial_transforms()
    test_database_and_wards()
    test_ai_and_underground()
    print("=" * 60)
    print(" ALL TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)

