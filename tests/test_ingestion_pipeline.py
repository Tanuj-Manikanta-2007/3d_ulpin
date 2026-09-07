"""
test_ingestion_pipeline.py
Automated test suite for the Dual-Path 3D Ingestion, Segmentation,
Floor Plan Vectorization, 3D Topology, and Elevation Pipeline.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shapely.geometry import Polygon
from backend.services.lidar_pipeline import lidar_pipeline_instance
from backend.services.floorplan_vectorizer import floorplan_vectorizer_instance
from backend.services.elevation_service import elevation_service_instance
from backend.services.spatial_service import (
    check_building_encroachment,
    check_3d_vertical_overlap,
    validate_3d_cadastral_topology
)
from backend.database.db import db_instance


def test_lidar_pipeline():
    print("Testing LiDAR Pipeline (Branch A)...")
    # Synthetic scan parsing
    pc = lidar_pipeline_instance.parse_point_cloud(b"", "flight_scan.laz")
    assert pc["status"] == "success"
    assert pc["point_count"] > 100
    print(f"  [PASS] Parsed {pc['point_count']} point cloud points.")

    # Segmentation
    segmented = lidar_pipeline_instance.segment_point_cloud(pc)
    assert segmented["ground_elevation_msl"] > 400.0
    assert segmented["ndsm_height_m"] > 5.0
    print(f"  [PASS] nDSM Height: {segmented['ndsm_height_m']}m, Ground MSL: {segmented['ground_elevation_msl']}m")

    # Facade floor slicing
    floors = lidar_pipeline_instance.slice_vertical_floors(segmented, "832454DYJFAQY2")
    assert len(floors) >= 3
    assert floors[0]["ulpin_3d"].endswith("-F00")
    assert len(floors[0]["ulpin_3d"]) == 18
    print(f"  [PASS] Extracted {len(floors)} vertical floor slices with 18-char 3D ULPINs.")

    # Footprint extraction
    footprint = lidar_pipeline_instance.extract_regularized_footprint(pc, [78.379, 17.439, 78.383, 17.443])
    assert footprint["area_sqm"] > 50.0
    print(f"  [PASS] Regularized 2D footprint extracted: {footprint['area_sqm']} m²")


def test_floorplan_vectorizer():
    print("Testing Architectural Floor Plan Vectorizer (Branch B)...")
    poly = Polygon([
        [78.3800, 17.4400], [78.3805, 17.4400],
        [78.3805, 17.4405], [78.3800, 17.4405],
        [78.3800, 17.4400]
    ])
    result = floorplan_vectorizer_instance.vectorize_floorplan(
        file_bytes=b"",
        filename="building_plan.pdf",
        building_polygon_wgs84=poly,
        floor_index=2,
        units_per_floor=4
    )
    assert result["status"] == "success"
    assert result["units_count"] >= 4
    assert result["total_floor_area_sqm"] > 500.0
    print(f"  [PASS] Vectorized {result['units_count']} units on Floor 2 ({result['total_floor_area_sqm']} m²).")


def test_elevation_service():
    print("Testing OpenTopography & Regional Copernicus 30m DEM Service...")
    elev = elevation_service_instance.get_baseline_elevation(17.4400, 78.3800)
    assert 480.0 <= elev["elevation_m_msl"] <= 560.0
    assert "elevation_m_msl" in elev
    print(f"  [PASS] Baseline Ground Elevation: {elev['elevation_m_msl']}m MSL ({elev['source']})")


def test_3d_topology_validation():
    print("Testing PostGIS / Shapely 3D Topology & Encroachment Engine...")
    parcel_poly = Polygon([
        [78.4800, 17.3800], [78.4810, 17.3800],
        [78.4810, 17.3810], [78.4800, 17.3810],
        [78.4800, 17.3800]
    ])
    # Compliant building inside parcel
    clean_bldg = Polygon([
        [78.4802, 17.3802], [78.4808, 17.3802],
        [78.4808, 17.3808], [78.4802, 17.3808],
        [78.4802, 17.3802]
    ])
    # Compliant non-overlapping floor strata
    compliant_floors = [
        {"floor_label": "L00", "z_min": 510.0, "z_max": 513.2},
        {"floor_label": "L01", "z_min": 513.2, "z_max": 516.4},
        {"floor_label": "L02", "z_min": 516.4, "z_max": 519.6}
    ]
    report_clean = validate_3d_cadastral_topology(parcel_poly, clean_bldg, compliant_floors, "832454DYJFAQY2")
    assert report_clean["is_compliant"] is True
    assert report_clean["deed_status"] == "APPROVED_FOR_REGISTRATION"
    print(f"  [PASS] Compliant Title Verified: Deed Status = {report_clean['deed_status']}")

    # Conflicting floor strata with vertical collision
    colliding_floors = [
        {"floor_label": "L00", "z_min": 510.0, "z_max": 514.0},
        {"floor_label": "L01", "z_min": 512.0, "z_max": 516.0}  # Overlaps from 512 to 514
    ]
    v_report = check_3d_vertical_overlap(colliding_floors)
    assert v_report["has_collision"] is True
    assert v_report["status"] == "COLLISION_DETECTED"
    print(f"  [PASS] Collision Detection Verified: {v_report['collisions_count']} vertical collision caught.")


if __name__ == "__main__":
    print("=" * 65)
    print(" RUNNING 3D CADASTRE INGESTION & TOPOLOGY TEST SUITE")
    print("=" * 65)
    test_lidar_pipeline()
    test_floorplan_vectorizer()
    test_elevation_service()
    test_3d_topology_validation()
    print("=" * 65)
    print(" ALL PIPELINE TESTS PASSED SUCCESSFULLY!")
    print("=" * 65)
