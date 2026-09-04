"""
lidar_service.py
Synthetic 3D LiDAR point cloud generation, ASPRS standard classification,
and LAS/LAZ export using laspy.

ASPRS Classification Standard:
- 2: Ground (Terrain surface)
- 3: Low Vegetation
- 4: Medium Vegetation
- 5: High Vegetation (Trees)
- 6: Building (Roof, facade walls)
- 1: Unassigned / Noise
"""

import os
import random
import math
from typing import List, Dict, Any, Tuple
from shapely.geometry import Polygon, Point
import numpy as np
import laspy

from backend.services.spatial_service import to_utm


def generate_synthetic_lidar_points(
    parcel_wgs84: Polygon,
    buildings_wgs84: List[Dict[str, Any]],
    base_elevation: float,
    point_density: float = 2.0,  # points per square metre
    origin_utm: Tuple[float, float] = (0.0, 0.0)
) -> List[Dict[str, Any]]:
    """
    Generate realistic synthetic LiDAR point cloud for a parcel with terrain, buildings, and vegetation.
    """
    parcel_utm = to_utm(parcel_wgs84)
    ox, oy = origin_utm
    minx, miny, maxx, maxy = parcel_utm.bounds
    
    points = []
    
    # 1. Ground Points across the parcel
    grid_step = max(0.5, 1.0 / math.sqrt(point_density))
    x_coords = np.arange(minx, maxx, grid_step)
    y_coords = np.arange(miny, maxy, grid_step)
    
    b_polys_utm = [to_utm(b["geometry"]) for b in buildings_wgs84]
    
    for x in x_coords:
        for y in y_coords:
            pt = Point(x, y)
            if not parcel_utm.contains(pt):
                continue
                
            # Check if point falls inside a building footprint
            in_building = False
            for idx, b_poly in enumerate(b_polys_utm):
                if b_poly.contains(pt):
                    in_building = True
                    b_info = buildings_wgs84[idx]
                    floors = b_info.get("floors", 3)
                    height = floors * b_info.get("floor_height", 3.2)
                    roof_z = base_elevation + height
                    
                    # Roof point
                    roof_noise = random.gauss(0, 0.03)
                    points.append({
                        "x": round(x - ox, 3),
                        "y": round(roof_z + roof_noise, 3),
                        "z": round(-(y - oy), 3),
                        "raw_x": round(x, 2),
                        "raw_y": round(y, 2),
                        "raw_z": round(roof_z + roof_noise, 2),
                        "intensity": int(random.uniform(160, 240)),
                        "classification": 6,  # Building
                        "class_name": "Building Roof"
                    })
                    break
                    
            if not in_building:
                # Bare ground terrain point
                ground_z = base_elevation + random.gauss(0, 0.04)
                points.append({
                    "x": round(x - ox, 3),
                    "y": round(ground_z, 3),
                    "z": round(-(y - oy), 3),
                    "raw_x": round(x, 2),
                    "raw_y": round(y, 2),
                    "raw_z": round(ground_z, 2),
                    "intensity": int(random.uniform(50, 110)),
                    "classification": 2,  # Ground
                    "class_name": "Ground Terrain"
                })
                
                # Occasional vegetation around the parcel boundary
                if random.random() < 0.08:
                    tree_height = random.uniform(3.0, 7.5)
                    tree_points_count = random.randint(4, 9)
                    for _ in range(tree_points_count):
                        tx = x + random.uniform(-0.8, 0.8)
                        ty = y + random.uniform(-0.8, 0.8)
                        tz = ground_z + random.uniform(1.2, tree_height)
                        points.append({
                            "x": round(tx - ox, 3),
                            "y": round(tz, 3),
                            "z": round(-(ty - oy), 3),
                            "raw_x": round(tx, 2),
                            "raw_y": round(ty, 2),
                            "raw_z": round(tz, 2),
                            "intensity": int(random.uniform(120, 180)),
                            "classification": 5,  # High Vegetation
                            "class_name": "Vegetation"
                        })

    # 2. Building Facade / Wall Points
    for idx, b_poly in enumerate(b_polys_utm):
        b_info = buildings_wgs84[idx]
        floors = b_info.get("floors", 3)
        height = floors * b_info.get("floor_height", 3.2)
        roof_z = base_elevation + height
        
        exterior_coords = list(b_poly.exterior.coords)
        for i in range(len(exterior_coords) - 1):
            p1 = exterior_coords[i]
            p2 = exterior_coords[i + 1]
            seg_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            num_steps = max(2, int(seg_len * 1.5))
            
            for s in range(num_steps):
                t = s / num_steps
                wx = p1[0] + t * (p2[0] - p1[0]) + random.gauss(0, 0.02)
                wy = p1[1] + t * (p2[1] - p1[1]) + random.gauss(0, 0.02)
                
                # Sample along the vertical wall
                num_z_steps = max(3, int(height * 0.8))
                for z_step in range(num_z_steps):
                    wz = base_elevation + (z_step / num_z_steps) * height
                    points.append({
                        "x": round(wx - ox, 3),
                        "y": round(wz, 3),
                        "z": round(-(wy - oy), 3),
                        "raw_x": round(wx, 2),
                        "raw_y": round(wy, 2),
                        "raw_z": round(wz, 2),
                        "intensity": int(random.uniform(140, 200)),
                        "classification": 6,  # Building
                        "class_name": "Building Wall"
                    })

    return points


def export_points_to_las(
    points: List[Dict[str, Any]],
    output_filepath: str
) -> str:
    """
    Write LiDAR points to a standard LAS file using laspy.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_filepath)), exist_ok=True)
    
    header = laspy.LasHeader(point_format=2, version="1.2")
    header.scales = [0.001, 0.001, 0.001]
    header.offsets = [
        min(p["raw_x"] for p in points),
        min(p["raw_y"] for p in points),
        min(p["raw_z"] for p in points)
    ]
    
    las = laspy.LasData(header)
    las.x = np.array([p["raw_x"] for p in points])
    las.y = np.array([p["raw_y"] for p in points])
    las.z = np.array([p["raw_z"] for p in points])
    las.intensity = np.array([p["intensity"] for p in points], dtype=np.uint16)
    las.classification = np.array([p["classification"] for p in points], dtype=np.uint8)
    
    las.write(output_filepath)
    return output_filepath
