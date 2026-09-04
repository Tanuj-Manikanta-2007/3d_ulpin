"""
db.py
In-memory and file-backed spatial storage layer for Wards, Cadastral Parcels,
3D Buildings, and LiDAR caches.
Supports both Live OpenStreetMap (OSM) building ingestion and synthetic cadastral generation.
"""

import os
import json
import threading
from typing import List, Dict, Any, Optional
from shapely.geometry import shape, Polygon, MultiPolygon

from backend.services.spatial_service import (
    validate_and_fix_polygon,
    get_bbox_wgs84,
    geojson_to_shapely
)
from backend.services.dataset_generator import (
    partition_ward_into_parcels,
    generate_parcels_from_osm
)
from backend.services.lidar_service import generate_synthetic_lidar_points


class SpatialDatabase:
    def __init__(self, geojson_path: Optional[str] = None):
        self._lock = threading.RLock()
        self.wards: Dict[str, Dict[str, Any]] = {}
        self.parcels: Dict[str, Dict[str, Any]] = {}  # keyed by parcel_id
        self.ulpin_index: Dict[str, str] = {}         # ULPIN -> parcel_id
        self.lidar_cache: Dict[str, List[Dict[str, Any]]] = {} # parcel_id -> points
        
        # Default data path
        if geojson_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.geojson_path = os.path.join(base_dir, "data", "wards_hyderabad.geojson")
        else:
            self.geojson_path = geojson_path

        self.load_wards()

    def load_wards(self):
        """Load 145 Hyderabad wards from GeoJSON."""
        with self._lock:
            if not os.path.exists(self.geojson_path):
                print(f"[Warning] Wards file not found at {self.geojson_path}")
                return

            try:
                with open(self.geojson_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                features = data.get("features", [])
                for idx, feat in enumerate(features):
                    ward_id = str(feat.get("id", idx))
                    props = feat.get("properties", {})
                    name = props.get("Name", f"Ward {ward_id}")
                    raw_geom_dict = feat.get("geometry", {})
                    
                    try:
                        # Normalize coordinates to standard RFC 7946 [lon, lat]
                        sh_geom = geojson_to_shapely(raw_geom_dict)
                        norm_geom_dict = shapely_to_geojson(sh_geom)
                        bbox = list(sh_geom.bounds)  # min_lon, min_lat, max_lon, max_lat
                    except Exception:
                        norm_geom_dict = raw_geom_dict
                        bbox = [78.2, 17.2, 78.6, 17.6]

                    self.wards[ward_id] = {
                        "id": ward_id,
                        "name": name,
                        "properties": props,
                        "geometry": norm_geom_dict,
                        "bbox": bbox,
                        "parcels_count": 0
                    }
                print(f"[Database] Loaded {len(self.wards)} Hyderabad wards from GeoJSON.")
            except Exception as e:
                print(f"[Database] Error reading wards GeoJSON: {e}")

    def get_all_wards(self) -> List[Dict[str, Any]]:
        """Return list of all wards with parcel counts."""
        with self._lock:
            result = []
            for w_id, w in self.wards.items():
                p_count = sum(1 for p in self.parcels.values() if p["ward_id"] == w_id)
                w_copy = dict(w)
                w_copy["parcels_count"] = p_count
                result.append(w_copy)
            return sorted(result, key=lambda x: (x["parcels_count"] == 0, x["name"]))

    def get_ward(self, ward_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific ward by ID."""
        with self._lock:
            w = self.wards.get(str(ward_id))
            if w:
                p_count = sum(1 for p in self.parcels.values() if p["ward_id"] == str(ward_id))
                w_copy = dict(w)
                w_copy["parcels_count"] = p_count
                return w_copy
            return None

    def generate_ward_parcels(
        self,
        ward_id: str,
        target_parcels: int = 16,
        source: str = "osm"
    ) -> List[Dict[str, Any]]:
        """
        Generate cadastral parcels, 3D buildings, and 3D ULPINs for a ward.
        Supports 'osm' (Live Overpass API) or 'synthetic' (Voronoi partitioning).
        """
        with self._lock:
            ward = self.wards.get(str(ward_id))
            if not ward:
                raise ValueError(f"Ward with ID {ward_id} not found")

            # Remove existing parcels for this ward
            existing_ids = [p_id for p_id, p in self.parcels.items() if p["ward_id"] == str(ward_id)]
            for pid in existing_ids:
                if pid in self.parcels:
                    ulpin = self.parcels[pid]["ulpin"]
                    self.ulpin_index.pop(ulpin, None)
                    self.lidar_cache.pop(pid, None)
                    del self.parcels[pid]

            ward_geom_dict = ward["geometry"]
            sh_geom = geojson_to_shapely(ward_geom_dict)
            
            if isinstance(sh_geom, MultiPolygon):
                sh_geom = max(sh_geom.geoms, key=lambda g: g.area)

            if source.lower() == "osm":
                new_parcels = generate_parcels_from_osm(
                    ward_polygon_wgs84=sh_geom,
                    ward_id=ward_id,
                    max_parcels=None
                )
            else:
                new_parcels = partition_ward_into_parcels(
                    ward_polygon_wgs84=sh_geom,
                    ward_id=ward_id,
                    target_parcels=target_parcels
                )

            for p in new_parcels:
                pid = p["parcel_id"]
                ulpin = p["ulpin"]
                self.parcels[pid] = p
                self.ulpin_index[ulpin] = pid

            ward["parcels_count"] = len(new_parcels)
            return new_parcels

    def get_parcels(
        self,
        ward_id: Optional[str] = None,
        land_use: Optional[str] = None,
        search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Filter parcels by ward, land use, or search term (ULPIN, owner, survey no)."""
        with self._lock:
            results = list(self.parcels.values())
            
            if ward_id:
                ward_str = str(ward_id)
                results = [p for p in results if p["ward_id"] == ward_str]
                if len(results) == 0 and ward_str in self.wards:
                    # Auto-generate parcels for any selected ward on-the-fly
                    try:
                        results = self.generate_ward_parcels(ward_str, target_parcels=16, source="osm")
                    except Exception as e:
                        print(f"[Database] Auto-generation error for ward {ward_str}: {e}")
                        results = []

                
            if land_use and land_use.lower() != "all":
                results = [p for p in results if p["land_use"].lower() == land_use.lower()]
                
            if search:
                term = search.strip().upper()
                matched = []
                for p in results:
                    if (term in p["ulpin"].upper() or
                        term in p["parcel_id"].upper() or
                        term in p["owner_name"].upper() or
                        term in p["survey_number"].upper()):
                        matched.append(p)
                results = matched
                
            return results

    def get_parcel(self, parcel_id: str) -> Optional[Dict[str, Any]]:
        """Get parcel by ID or ULPIN."""
        with self._lock:
            if parcel_id in self.parcels:
                return self.parcels[parcel_id]
                
            pid = self.ulpin_index.get(parcel_id.strip().upper())
            if pid and pid in self.parcels:
                return self.parcels[pid]
                
            return None

    def get_parcel_lidar(self, parcel_id: str) -> List[Dict[str, Any]]:
        """Get or generate LiDAR points for a parcel."""
        with self._lock:
            if parcel_id in self.lidar_cache:
                return self.lidar_cache[parcel_id]

            parcel = self.get_parcel(parcel_id)
            if not parcel:
                raise ValueError(f"Parcel {parcel_id} not found")

            poly_wgs84 = geojson_to_shapely(parcel["geometry"])
            extrusion = parcel.get("extrusion") or {}
            base_elevation = extrusion.get("base_elevation_m", 510.0)
            origin_utm = tuple(extrusion.get("origin_utm", [0.0, 0.0]))
            
            buildings_wgs84 = []
            for b in extrusion.get("buildings", []):
                buildings_wgs84.append({
                    "geometry": poly_wgs84.buffer(-0.00003),
                    "floors": b.get("floors_count", 3),
                    "floor_height": 3.2
                })

            points = generate_synthetic_lidar_points(
                parcel_wgs84=poly_wgs84,
                buildings_wgs84=buildings_wgs84,
                base_elevation=base_elevation,
                point_density=1.5,
                origin_utm=origin_utm
            )
            
            self.lidar_cache[parcel_id] = points
            return points

    def get_stats(self) -> Dict[str, Any]:
        """Aggregate summary statistics across all parcels."""
        with self._lock:
            total_wards = len(self.wards)
            active_wards = len(set(p["ward_id"] for p in self.parcels.values()))
            total_parcels = len(self.parcels)
            
            total_buildings = 0
            total_3d_units = 0
            total_land_area = 0.0
            total_built_up_area = 0.0
            land_use_counts = {"Residential": 0, "Commercial": 0, "Mixed Use": 0, "Institutional": 0}

            for p in self.parcels.values():
                total_land_area += p.get("area_sqm", 0.0)
                lu = p.get("land_use", "Residential")
                land_use_counts[lu] = land_use_counts.get(lu, 0) + 1
                
                ext = p.get("extrusion") or {}
                b_list = ext.get("buildings", [])
                total_buildings += len(b_list)
                for b in b_list:
                    total_3d_units += len(b.get("floors", []))
                    total_built_up_area += b.get("built_up_area_sqm", 0.0)

            return {
                "total_wards": total_wards,
                "active_wards_with_parcels": active_wards,
                "total_parcels": total_parcels,
                "total_buildings": total_buildings,
                "total_3d_units": total_3d_units,
                "total_land_area_sqm": round(total_land_area, 2),
                "total_built_up_area_sqm": round(total_built_up_area, 2),
                "land_use_breakdown": land_use_counts
            }


# Global database singleton
db_instance = SpatialDatabase()

# Seed default flagship wards on startup
def seed_initial_wards():
    for wid in ["1", "0", "3"]:
        if wid in db_instance.wards:
            try:
                db_instance.generate_ward_parcels(wid, target_parcels=14, source="synthetic")
                print(f"[Seed] Generated default 3D parcels for Ward ID {wid} ({db_instance.wards[wid]['name']})")
            except Exception as e:
                print(f"[Seed] Note on seeding ward {wid}: {e}")

seed_initial_wards()
