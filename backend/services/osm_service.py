"""
osm_service.py
Fetches real-world OpenStreetMap (OSM) building footprints and road networks
for any selected Hyderabad ward boundary using the Overpass API.
"""

import requests
import json
from typing import List, Dict, Any, Optional, Tuple
from shapely.geometry import Polygon, MultiPolygon, Point
from shapely.validation import make_valid
import numpy as np

from backend.services.spatial_service import (
    validate_and_fix_polygon,
    calculate_metric_area,
    get_centroid_wgs84,
    shapely_to_geojson
)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter"
]


<<<<<<< HEAD
def query_overpass(query_str: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """Execute Overpass QL query with 5.0s timeout and automatic endpoint failover."""
=======
def query_overpass(query_str: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Execute Overpass QL query with rapid timeout and automatic fallback across endpoints."""
>>>>>>> d674a2a5c7876f346ceac71627e5a12456fc5451
    headers = {
        "User-Agent": "3D-ULPIN-Cadastral-Engine/2.0 (Hyderabad-Digital-Twin)",
        "Accept": "application/json"
    }
    
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            resp = requests.post(
                endpoint,
                data={"data": query_str},
                headers=headers,
                timeout=timeout
            )
            if resp.status_code == 200:
                return resp.json()
<<<<<<< HEAD
        except Exception:
=======
            else:
                print(f"[OSM Overpass] Endpoint {endpoint} returned status {resp.status_code}")
        except Exception as e:
            print(f"[OSM Overpass] Endpoint {endpoint} failed or timed out: {e}")
>>>>>>> d674a2a5c7876f346ceac71627e5a12456fc5451
            continue
    print("[OSM Overpass] All Overpass endpoints busy or rate-limited; falling back to synthetic cadastre.")
    return None


<<<<<<< HEAD
=======
def get_osm_building_count_in_bbox(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float
) -> int:
    """Execute a rapid Overpass count query to get the total number of real buildings across the ward bounding box."""
    query = f"""
    [out:json][timeout:15];
    (
      way["building"]({min_lat:.6f},{min_lon:.6f},{max_lat:.6f},{max_lon:.6f});
    );
    out count;
    """
    data = query_overpass(query, timeout=8)
    if data and "elements" in data and data["elements"]:
        tags = data["elements"][0].get("tags", {})
        count_val = int(tags.get("ways", tags.get("total", 0)))
        if count_val > 0:
            print(f"[OSM Overpass] Total buildings detected across ward bbox: {count_val}")
            return count_val
    return 0

>>>>>>> d674a2a5c7876f346ceac71627e5a12456fc5451

def fetch_osm_buildings_in_bbox(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    max_buildings: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Query Overpass API for real building footprints across the full bounding box.
    Uses 'out geom qt' to evenly sample buildings across the quadtree of the entire ward.
    Returns geometries in standard WGS84 [lon, lat] coordinate format.
    """
    limit_str = f" {max_buildings}" if max_buildings else ""
    query = f"""
    [out:json][timeout:45];
    (
      way["building"]({min_lat:.6f},{min_lon:.6f},{max_lat:.6f},{max_lon:.6f});
    );
    out geom{limit_str} qt;
    """

    data = query_overpass(query, timeout=30)
    if not data:
        # Fallback to standard body query if geom is not supported
        fallback_query = f"""
        [out:json][timeout:45];
        (
          way["building"]({min_lat:.6f},{min_lon:.6f},{max_lat:.6f},{max_lon:.6f});
        );
        out body{limit_str};
        >;
        out skel qt;
        """
        data = query_overpass(fallback_query, timeout=25)
        if not data:
            print("[OSM Service] No data returned from Overpass API")
            return []

    elements = data.get("elements", [])
    
    # 1. Index nodes if present (for fallback format)
    nodes = {}
    for el in elements:
        if el.get("type") == "node":
            nodes[el["id"]] = (el["lon"], el["lat"])

    # 2. Reconstruct building polygons from ways
    buildings = []
    for el in elements:
        if el.get("type") == "way" and "building" in el.get("tags", {}):
            coords = []
            
            # Format A: geometry coordinates embedded directly via 'out geom'
            if "geometry" in el and isinstance(el["geometry"], list) and len(el["geometry"]) >= 4:
                coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
            # Format B: node references via 'out body'
            elif "nodes" in el:
                node_ids = el.get("nodes", [])
                for nid in node_ids:
                    if nid in nodes:
                        coords.append(nodes[nid])

            if len(coords) >= 4:
                try:
                    poly = Polygon(coords)
                    poly = validate_and_fix_polygon(poly)
                    if poly.is_empty or poly.area <= 0:
                        continue

                    area_sqm = calculate_metric_area(poly)
                    if area_sqm < 20.0 or area_sqm > 50000.0:
                        continue

                    tags = el.get("tags", {})
                    
                    # Parse floors
                    floors = 1
                    if "building:levels" in tags:
                        try:
                            floors = int(float(tags["building:levels"]))
                        except ValueError:
                            floors = 3
                    elif "height" in tags:
                        try:
                            h_val = float(tags["height"].replace("m", "").strip())
                            floors = max(1, int(round(h_val / 3.2)))
                        except ValueError:
                            floors = 3
                    else:
                        b_type = tags.get("building", "yes").lower()
                        if b_type in ["apartments", "commercial", "office", "hospital"]:
                            floors = max(3, min(12, int(area_sqm / 180)))
                        elif b_type in ["residential", "house", "detached"]:
                            floors = max(1, min(4, int(area_sqm / 250)))
                        else:
                            floors = 3

                    floors = max(1, min(25, floors))
                    name = tags.get("name") or tags.get("addr:housename") or f"OSM Building {el['id']}"
                    
                    land_use = "Residential"
                    b_tag = tags.get("building", "").lower()
                    if b_tag in ["commercial", "retail", "office", "hotel"]:
                        land_use = "Commercial"
                    elif b_tag in ["apartments", "mixed"]:
                        land_use = "Mixed Use"
                    elif b_tag in ["school", "university", "hospital", "public", "civic", "government"]:
                        land_use = "Institutional"

                    buildings.append({
                        "osm_id": el["id"],
                        "geometry": poly,
                        "area_sqm": round(area_sqm, 2),
                        "floors": floors,
                        "floor_height": 3.2,
                        "name": name,
                        "land_use": land_use,
                        "tags": tags
                    })

                    if max_buildings is not None and len(buildings) >= max_buildings:
                        break
                except Exception:
                    continue

    print(f"[OSM Service] Successfully parsed {len(buildings)} real OSM building footprints across ward.")
    return buildings

