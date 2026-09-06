"""
dataset_generator.py
Generates cadastral parcels, building footprints, land-use classifications,
and 3D ULPIN identities within Hyderabad administrative wards.

Sources supported:
1. 'osm': Fetches real-world building footprints from OpenStreetMap (Overpass API)
   and creates cadastral parcels around them with realistic legal boundaries.
2. 'synthetic': Algorithmic Voronoi spatial partitioning of the ward polygon.
"""

import random
from typing import List, Dict, Any, Optional, Union
from shapely.geometry import Polygon, MultiPolygon, Point, MultiPoint
from shapely.ops import voronoi_diagram
from shapely.validation import make_valid
import numpy as np

from backend.services.spatial_service import (
    to_utm,
    to_wgs84,
    validate_and_fix_polygon,
    calculate_metric_area,
    get_centroid_wgs84,
    shapely_to_geojson
)
from backend.services.ulpin_generator import generate_prototype_ulpin
from backend.services.extrusion_engine import extrude_parcel_and_buildings
from backend.services.osm_service import fetch_osm_buildings_in_bbox, get_osm_building_count_in_bbox


LAND_USES = ["Residential", "Commercial", "Mixed Use", "Institutional"]
OWNER_FIRST_NAMES = [
    "Ramesh", "Suresh", "Lakshmi", "Venkat", "Ananya", "Prasad", "Kavitha",
    "Srikanth", "Srinivas", "Madhavi", "Rajesh", "Pooja", "Vikram", "Deepa",
    "Sai", "Krishna", "Arun", "Swathi", "Goutham", "Harika", "Telangana State Infra"
]
OWNER_LAST_NAMES = [
    "Reddy", "Rao", "Sharma", "Varma", "Goud", "Chowdary", "Gupta",
    "Patel", "Naidu", "Yadav", "Kumar", "Corporation", "Estates Pvt Ltd"
]


def generate_random_owner(land_use: str) -> str:
    """Generate realistic owner name or entity."""
    if land_use == "Institutional":
        return "GHMC / Govt of Telangana (Public Asset)"
    elif land_use == "Commercial" and random.random() < 0.4:
        return f"{random.choice(OWNER_FIRST_NAMES)} Commercial Properties Ltd"
    return f"{random.choice(OWNER_FIRST_NAMES)} {random.choice(OWNER_LAST_NAMES)}"


def generate_parcels_from_osm(
    ward_polygon_wgs84: Polygon,
    ward_id: Union[int, str],
    max_parcels: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Fetch real building footprints from OSM across the entire ward boundary.
    All geometries are standard WGS84 (lon, lat).
    Generates all building parcels in the ward, tagging 50 representative parcels
    to store into PostgreSQL.
    """
    min_lon, min_lat, max_lon, max_lat = ward_polygon_wgs84.bounds
    
    # 1. Total real building count across the entire ward bounding box
    total_osm_count = get_osm_building_count_in_bbox(min_lon, min_lat, max_lon, max_lat)
    
    # 2. Fetch real building footprints across the full bounding box
    osm_buildings = fetch_osm_buildings_in_bbox(
        min_lon=min_lon,
        min_lat=min_lat,
        max_lon=max_lon,
        max_lat=max_lat,
        max_buildings=max_parcels
    )

    if not osm_buildings:
        print("[Dataset Generator] No OSM buildings found in ward bounds, falling back to synthetic generator.")
        return partition_ward_into_parcels(
            ward_polygon_wgs84,
            ward_id,
            target_parcels=max_parcels or 1500
        )

    # 3. Filter buildings that strictly intersect the ward polygon boundary
    valid_buildings = []
    for b_item in osm_buildings:
        b_poly = b_item["geometry"]
        if ward_polygon_wgs84.intersects(b_poly):
            valid_buildings.append(b_item)

    if not valid_buildings:
        print("[Dataset Generator] 0 OSM buildings fell strictly inside ward polygon, falling back to synthetic generator.")
        return partition_ward_into_parcels(
            ward_polygon_wgs84,
            ward_id,
            target_parcels=max_parcels or 1500
        )

    # Total detected building count across the ward
    total_detected = max(len(valid_buildings), total_osm_count)
    print(f"[Dataset Generator] Detected {total_detected} buildings in Ward {ward_id} (OSM). Generating all {len(valid_buildings)} parcels, with 50 selected for DB persistence.")

    # 4. Generate parcels for ALL valid buildings in the ward, tagging 50 for database persistence
    target_db_count = min(50, len(valid_buildings))
    if len(valid_buildings) > target_db_count:
        step = len(valid_buildings) / float(target_db_count)
        persisted_indices = set(int(i * step) for i in range(target_db_count))
    else:
        persisted_indices = set(range(len(valid_buildings)))

    parcels_data = []
    parcel_count = 0

    for idx, b_item in enumerate(valid_buildings):
        b_poly = b_item["geometry"]
        parcel_count += 1
        is_persisted = (idx in persisted_indices)
        
        # Fast parcel boundary in WGS84 (0.00005 to 0.000085 deg ≈ 5.5m to 9.5m buffer)
        buffer_deg = random.uniform(0.00005, 0.000085)
        parcel_wgs84 = b_poly.buffer(buffer_deg)
        
        centroid = parcel_wgs84.centroid
        centroid_lat, centroid_lon = float(centroid.y), float(centroid.x)
        
        b_area = b_item.get("area_sqm") or 120.0
        area_sqm = round(b_area * 1.6, 2)

        # 2D Base ULPIN
        parcel_ulpin = generate_prototype_ulpin(centroid_lat, centroid_lon, floor=0)

        land_use = b_item.get("land_use", "Residential")
        owner = generate_random_owner(land_use)
        survey_no = f"Sy. {random.randint(12, 380)}/{random.randint(1, 8)}"
        w_prefix = str(ward_id) if "-" in str(ward_id) else f"HYD-W{ward_id}"
        parcel_id = f"{w_prefix}-OSM{parcel_count:03d}"

        # Eagerly compute full 3D extrusion for the 50 DB parcels
        if is_persisted:
            parcel_wgs84_fixed = validate_and_fix_polygon(parcel_wgs84)
            extrusion_data = extrude_parcel_and_buildings(
                parcel_wgs84=parcel_wgs84_fixed,
                buildings_wgs84=[{
                    "geometry": b_poly,
                    "floors": b_item.get("floors", 3),
                    "floor_height": 3.2,
                    "name": b_item.get("name", f"Building {parcel_count}")
                }],
                parcel_ulpin=parcel_ulpin,
                parcel_id=parcel_id,
                land_use=land_use,
                owner_name=owner
            )
        else:
            extrusion_data = None

        # Store JSON-serializable buildings_wgs84 (GeoJSON geometry)
        buildings_geojson = [{
            "geometry": shapely_to_geojson(b_poly),
            "floors": b_item.get("floors", 3),
            "floor_height": 3.2,
            "name": b_item.get("name", f"Building {parcel_count}")
        }]

        parcels_data.append({
            "parcel_id": parcel_id,
            "ward_id": str(ward_id),
            "ulpin": parcel_ulpin,
            "survey_number": survey_no,
            "land_use": land_use,
            "owner_name": owner,
            "area_sqm": area_sqm,
            "centroid": {"lat": centroid_lat, "lon": centroid_lon},
            "geometry": shapely_to_geojson(parcel_wgs84),
            "buildings_count": 1,
            "floors_count": b_item.get("floors", 3),
            "data_source": "OpenStreetMap Live",
            "total_detected_in_ward": total_detected,
            "is_persisted_to_db": is_persisted,
            "buildings_wgs84": buildings_geojson,
            "extrusion": extrusion_data
        })

    return parcels_data


def partition_ward_into_parcels(
    ward_polygon_wgs84: Polygon,
    ward_id: Union[int, str],
    target_parcels: int = 1200
) -> List[Dict[str, Any]]:
    """
    Subdivide a study ward into discrete cadastral parcels using Voronoi spatial tessellation in UTM projection.
    Generates a dense network of parcels across the ward, tagging 50 for database persistence.
    """
    ward_utm = to_utm(ward_polygon_wgs84)
    minx, miny, maxx, maxy = ward_utm.bounds
    
    # Generate seed points within the ward interior
    seed_points = []
    attempts = 0
    while len(seed_points) < target_parcels and attempts < target_parcels * 30:
        attempts += 1
        rx = random.uniform(minx, maxx)
        ry = random.uniform(miny, maxy)
        pt = Point(rx, ry)
        if ward_utm.contains(pt):
            seed_points.append(pt)
            
    if len(seed_points) < 4:
        seed_points = [
            Point(minx + (maxx - minx) * fx, miny + (maxy - miny) * fy)
            for fx in [0.2, 0.4, 0.6, 0.8] for fy in [0.2, 0.4, 0.6, 0.8]
        ]

    multi_pt = MultiPoint(seed_points)
    voronoi_cells = voronoi_diagram(multi_pt, envelope=ward_utm.envelope)
    
    parcels_data = []
    parcel_count = 0
    
    for cell in voronoi_cells.geoms:
        clipped = cell.intersection(ward_utm)
        if clipped.is_empty:
            continue
            
        if isinstance(clipped, MultiPolygon):
            clipped = max(clipped.geoms, key=lambda g: g.area)
            
        if clipped.area < 150.0:
            continue

        parcel_count += 1
        poly_wgs84 = to_wgs84(clipped)
        poly_wgs84 = validate_and_fix_polygon(poly_wgs84)
        
        centroid_lat, centroid_lon = get_centroid_wgs84(poly_wgs84)
        area_sqm = calculate_metric_area(poly_wgs84)

        parcel_ulpin = generate_prototype_ulpin(centroid_lat, centroid_lon, floor=0)
        
        land_use = random.choices(LAND_USES, weights=[0.55, 0.25, 0.15, 0.05])[0]
        owner = generate_random_owner(land_use)
        survey_no = f"Sy. {random.randint(12, 380)}/{random.randint(1, 8)}"
        w_prefix = str(ward_id) if "-" in str(ward_id) else f"HYD-W{ward_id}"
        parcel_id = f"{w_prefix}-P{parcel_count:03d}"
        
        # Setback buffer in UTM (3m - 5.5m)
        setback_dist = random.uniform(3.0, 5.5)
        b_utm = clipped.buffer(-setback_dist)
        
        buildings_wgs84 = []
        if not b_utm.is_empty and b_utm.area > 50.0:
            if isinstance(b_utm, MultiPolygon):
                b_utm_geom = max(b_utm.geoms, key=lambda g: g.area)
            else:
                b_utm_geom = b_utm
                
            b_poly_wgs84 = to_wgs84(b_utm_geom)
            
            if land_use == "Residential":
                floors = random.choices([1, 2, 3, 4, 5, 8], weights=[0.15, 0.35, 0.25, 0.15, 0.07, 0.03])[0]
            elif land_use == "Commercial":
                floors = random.choices([3, 4, 6, 8, 10, 14], weights=[0.15, 0.25, 0.25, 0.20, 0.10, 0.05])[0]
            elif land_use == "Mixed Use":
                floors = random.choices([4, 5, 6, 8, 12], weights=[0.20, 0.30, 0.25, 0.15, 0.10])[0]
            else:
                floors = random.choice([2, 3, 4])
                
            buildings_wgs84.append({
                "geometry": b_poly_wgs84,
                "floors": floors,
                "floor_height": 3.2,
                "name": f"Structure-{parcel_count}"
            })

        extrusion_data = extrude_parcel_and_buildings(
            parcel_wgs84=poly_wgs84,
            buildings_wgs84=buildings_wgs84,
            parcel_ulpin=parcel_ulpin,
            parcel_id=parcel_id,
            land_use=land_use,
            owner_name=owner
        )
        
        buildings_geojson = []
        for b in buildings_wgs84:
            bg = b.get("geometry")
            bg_json = shapely_to_geojson(bg) if hasattr(bg, "__geo_interface__") else bg
            buildings_geojson.append({
                "geometry": bg_json,
                "floors": b.get("floors", 1),
                "floor_height": b.get("floor_height", 3.2),
                "name": b.get("name", f"Structure-{parcel_count}")
            })

        parcels_data.append({
            "parcel_id": parcel_id,
            "ward_id": str(ward_id),
            "ulpin": parcel_ulpin,
            "survey_number": survey_no,
            "land_use": land_use,
            "owner_name": owner,
            "area_sqm": round(area_sqm, 2),
            "centroid": {"lat": centroid_lat, "lon": centroid_lon},
            "geometry": shapely_to_geojson(poly_wgs84),
            "buildings_count": len(buildings_wgs84),
            "floors_count": sum(b.get("floors", 1) for b in buildings_wgs84),
            "data_source": "Synthetic Partitioning",
            "buildings_wgs84": buildings_geojson,
            "extrusion": extrusion_data
        })

    # Tag 50 evenly sampled parcels for DB persistence
    target_db_count = min(50, len(parcels_data))
    if len(parcels_data) > target_db_count:
        step = len(parcels_data) / float(target_db_count)
        persisted_indices = set(int(i * step) for i in range(target_db_count))
    else:
        persisted_indices = set(range(len(parcels_data)))

    for idx, p in enumerate(parcels_data):
        p["is_persisted_to_db"] = (idx in persisted_indices)
        p["total_detected_in_ward"] = len(parcels_data)
        
    return parcels_data
