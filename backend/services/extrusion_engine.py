"""
extrusion_engine.py
3D Geometry extrusion, DEM terrain ground elevation, DSM roof elevation estimation,
multi-story floor slicing, and Three.js 3D mesh generation.

Concepts:
- Base Elevation (DEM): Ground surface elevation Z_base (Hyderabad plateau ~490m-560m MSL)
- Surface Elevation (DSM): Roof level elevation Z_roof = Z_base + building_height
- 3D Extrusion: Extrudes 2D polygon footprint vertically from Z_base to Z_roof
- Floor Slicing: Divides volumetric building into discrete floor units (L00, L01, L02...)
- 3D ULPIN: Floor-specific unique identifier encoded with floor index
"""

import math
from typing import List, Dict, Any, Tuple
from shapely.geometry import Polygon, MultiPolygon
import numpy as np

from backend.services.spatial_service import (
    to_utm,
    to_wgs84,
    get_centroid_wgs84,
    calculate_metric_area
)
from backend.services.ulpin_generator import generate_prototype_ulpin


def estimate_terrain_elevation(lat: float, lon: float) -> float:
    """
    Simulate DEM terrain elevation (metres above MSL) for Hyderabad region.
    Smooth elevation variation across the Hyderabad topography (approx 490m to 545m).
    """
    # Hyderabad center approx 17.40, 78.48
    d_lat = (lat - 17.40) * 111000.0  # metres
    d_lon = (lon - 78.48) * 105000.0  # metres
    
    # Smooth topographic wave model
    elevation = 512.0 + 18.0 * math.sin(d_lat / 4000.0) + 15.0 * math.cos(d_lon / 3500.0) + 5.0 * math.sin((d_lat + d_lon) / 2000.0)
    return round(float(elevation), 2)


def generate_3d_floors(
    building_footprint_wgs84: Polygon,
    parcel_ulpin: str,
    base_elevation: float,
    floors_count: int,
    floor_height: float = 3.2,
    building_name: str = "Structure",
    land_use: str = "Residential"
) -> List[Dict[str, Any]]:
    """
    Slice an extruded building volume into distinct 3D floor units.
    Each floor gets:
      - floor_index (0 for L00/Ground, 1 for L01, 2 for L02...)
      - floor_label ('L00 (Ground)', 'L01', 'L02'...)
      - z_min (base elevation of floor)
      - z_max (top elevation of floor)
      - floor_3d_ulpin (DoLR/NIC 3D ULPIN with floor code)
      - floor_area_sqm (carpet area of the floor)
    """
    centroid_lat, centroid_lon = get_centroid_wgs84(building_footprint_wgs84)
    floor_area = calculate_metric_area(building_footprint_wgs84)
    
    floors = []
    base_14 = parcel_ulpin[:14] if len(parcel_ulpin) >= 14 else parcel_ulpin

    for f_idx in range(floors_count):
        z_min = base_elevation + (f_idx * floor_height)
        z_max = z_min + floor_height
        floor_label = f"L{f_idx:02d}" if f_idx > 0 else "L00 (Ground)"
        
        # 18-character 3D ULPIN: 14-char 2D parcel base + 4-char vertical level suffix (-F00, -F01, -F04)
        floor_ulpin_3d = f"{base_14}-F{f_idx:02d}"
        
        floors.append({
            "floor_index": f_idx,
            "floor_label": floor_label,
            "z_min": round(z_min, 2),
            "z_max": round(z_max, 2),
            "height": round(floor_height, 2),
            "area_sqm": round(floor_area, 2),
            "ulpin_2d_base": base_14,
            "ulpin_3d": floor_ulpin_3d,
            "unit_type": f"{land_use} Apartment Unit",
            "unit_id": f"{base_14}-F{f_idx:02d}",
        })
        
    return floors


def generate_underground_units(
    parcel_wgs84: Polygon,
    parcel_ulpin: str,
    base_elevation: float,
    underground_levels: int = 2
) -> List[Dict[str, Any]]:
    """
    Generate sub-surface 3D volumetric units (Basements, Underground Infrastructure, Utility Corridors).
    Sub-surface elevations are below ground base elevation (Z < base_elevation).
    Each unit receives an 18-character 3D ULPIN (14-char 2D base + 4-char underground suffix).
    """
    centroid_lat, centroid_lon = get_centroid_wgs84(parcel_wgs84)
    parcel_area = calculate_metric_area(parcel_wgs84)
    underground_area = parcel_area * 0.85
    base_14 = parcel_ulpin[:14] if len(parcel_ulpin) >= 14 else parcel_ulpin

    units = []

    # Basement Parking & Storage (B01, B02...)
    for b_idx in range(1, underground_levels + 1):
        depth = b_idx * 3.5
        z_max = base_elevation - (b_idx - 1) * 3.5
        z_min = base_elevation - depth
        label = f"B{b_idx:02d} (Basement Level {b_idx})"
        
        # 18-character 3D ULPIN: 14-char 2D parcel base + 4-char basement suffix (-B01, -B02)
        undg_ulpin = f"{base_14}-B{b_idx:02d}"

        units.append({
            "domain": "Underground",
            "unit_id": f"{base_14}-B{b_idx:02d}",
            "label": label,
            "category": "Parking & Vehicle Storage" if b_idx == 1 else "Building Utility & HVAC",
            "z_min": round(z_min, 2),
            "z_max": round(z_max, 2),
            "depth_m": 3.5,
            "area_sqm": round(underground_area, 2),
            "volume_cu_m": round(underground_area * 3.5, 2),
            "ulpin_2d_base": base_14,
            "ulpin_3d": undg_ulpin,
            "structured_ulpin": undg_ulpin
        })

    # Underground Utility Corridor (Water / Electrical / Metro Conduit)
    util_depth_min = base_elevation - 12.0
    util_depth_max = base_elevation - 9.0
    util_ulpin = f"{base_14}-UTL"
    
    units.append({
        "domain": "Underground",
        "unit_id": f"{base_14}-UTL",
        "label": "SUB-UTIL (Utility & Infrastructure Conduit)",
        "category": "Municipal Utility / Metro Right-of-Way",
        "z_min": round(util_depth_min, 2),
        "z_max": round(util_depth_max, 2),
        "depth_m": 3.0,
        "area_sqm": round(parcel_area * 0.35, 2),
        "volume_cu_m": round(parcel_area * 0.35 * 3.0, 2),
        "ulpin_2d_base": base_14,
        "ulpin_3d": util_ulpin,
        "structured_ulpin": util_ulpin
    })

    return units




def generate_3d_mesh(
    footprint_utm: Polygon,
    z_min: float,
    z_max: float,
    origin_utm: Tuple[float, float] = (0.0, 0.0)
) -> Dict[str, Any]:
    """
    Generate 3D mesh data (vertices and triangular faces) for Three.js rendering.
    Coordinates are normalized relative to origin_utm for local 3D rendering.
    """
    coords = list(footprint_utm.exterior.coords)
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]  # drop duplicate closing vertex
        
    num_verts = len(coords)
    if num_verts < 3:
        return {"vertices": [], "faces": []}

    ox, oy = origin_utm
    vertices = []
    
    # Bottom vertices (0 to num_verts-1)
    for x, y in coords:
        vertices.append([round(x - ox, 3), round(z_min, 3), round(-(y - oy), 3)])
        
    # Top vertices (num_verts to 2*num_verts-1)
    for x, y in coords:
        vertices.append([round(x - ox, 3), round(z_max, 3), round(-(y - oy), 3)])

    faces = []
    
    # Side wall quad faces (triangulated)
    for i in range(num_verts):
        next_i = (i + 1) % num_verts
        
        b1 = i
        b2 = next_i
        t1 = i + num_verts
        t2 = next_i + num_verts
        
        # Quad (b1, b2, t2, t1) as two triangles
        faces.append([b1, b2, t2])
        faces.append([b1, t2, t1])
        
    # Roof and floor cap triangulation (fan triangulation for simple polygons)
    # Bottom cap (reversed winding)
    for i in range(1, num_verts - 1):
        faces.append([0, i + 1, i])
        
    # Top cap
    for i in range(1, num_verts - 1):
        faces.append([num_verts, num_verts + i, num_verts + i + 1])

    return {
        "vertices": vertices,
        "faces": faces,
        "z_min": z_min,
        "z_max": z_max,
        "height": round(z_max - z_min, 2)
    }


def extrude_parcel_and_buildings(
    parcel_wgs84: Polygon,
    buildings_wgs84: List[Dict[str, Any]],
    parcel_ulpin: str,
    parcel_id: str,
    land_use: str = "Residential",
    owner_name: str = "Unknown"
) -> Dict[str, Any]:
    """
    Extrude parcel terrain base and building structures with multi-story floor slicing.
    """
    centroid_lat, centroid_lon = get_centroid_wgs84(parcel_wgs84)
    base_elevation = estimate_terrain_elevation(centroid_lat, centroid_lon)
    parcel_area = calculate_metric_area(parcel_wgs84)
    parcel_utm = to_utm(parcel_wgs84)
    
    origin_utm = (parcel_utm.centroid.x, parcel_utm.centroid.y)
    
    processed_buildings = []
    total_built_up_area = 0.0
    max_height = 0.0
    
    for b_idx, b_info in enumerate(buildings_wgs84):
        b_poly_wgs84 = b_info["geometry"]
        floors_count = b_info.get("floors", 3)
        floor_height = b_info.get("floor_height", 3.2)
        total_height = floors_count * floor_height
        roof_elevation = base_elevation + total_height
        
        if total_height > max_height:
            max_height = total_height
            
        b_poly_utm = to_utm(b_poly_wgs84)
        b_mesh = generate_3d_mesh(b_poly_utm, base_elevation, roof_elevation, origin_utm)
        
        floors = generate_3d_floors(
            building_footprint_wgs84=b_poly_wgs84,
            parcel_ulpin=parcel_ulpin,
            base_elevation=base_elevation,
            floors_count=floors_count,
            floor_height=floor_height,
            building_name=f"Block {chr(65 + b_idx)}",
            land_use=land_use
        )
        
        b_footprint_area = calculate_metric_area(b_poly_wgs84)
        b_total_area = b_footprint_area * floors_count
        total_built_up_area += b_total_area
        
        # Generate individual floor mesh segments
        floor_meshes = []
        for fl in floors:
            f_mesh = generate_3d_mesh(b_poly_utm, fl["z_min"], fl["z_max"], origin_utm)
            floor_meshes.append({
                "floor_index": fl["floor_index"],
                "floor_label": fl["floor_label"],
                "ulpin_3d": fl["ulpin_3d"],
                "mesh": f_mesh
            })
            
        processed_buildings.append({
            "building_id": f"{parcel_id}-B{b_idx + 1}",
            "building_name": b_info.get("name", f"Building {b_idx + 1}"),
            "floors_count": floors_count,
            "height_m": round(total_height, 2),
            "base_elevation_m": round(base_elevation, 2),
            "roof_elevation_m": round(roof_elevation, 2),
            "footprint_area_sqm": round(b_footprint_area, 2),
            "built_up_area_sqm": round(b_total_area, 2),
            "full_mesh": b_mesh,
            "floors": floors,
            "floor_meshes": floor_meshes
        })

    # Floor Area Ratio (FAR) / Floor Space Index (FSI)
    far = round(total_built_up_area / max(parcel_area, 1.0), 2)
    
    # Generate sub-surface underground infrastructure units
    underground_units = generate_underground_units(
        parcel_wgs84=parcel_wgs84,
        parcel_ulpin=parcel_ulpin,
        base_elevation=base_elevation,
        underground_levels=2
    )

    surface_ulpin = f"HYD-ULPIN-SURF-{parcel_ulpin[:8]}"
    
    return {
        "parcel_id": parcel_id,
        "parcel_ulpin": parcel_ulpin,
        "surface_ulpin": surface_ulpin,
        "land_use": land_use,
        "owner_name": owner_name,
        "parcel_area_sqm": round(parcel_area, 2),
        "base_elevation_m": round(base_elevation, 2),
        "max_height_m": round(max_height, 2),
        "far": far,
        "buildings_count": len(processed_buildings),
        "total_units_count": sum(b["floors_count"] for b in processed_buildings) + len(underground_units),
        "buildings": processed_buildings,
        "underground_units": underground_units,
        "origin_utm": [round(origin_utm[0], 2), round(origin_utm[1], 2)]
    }

