"""
models.py
Pydantic data schemas for Ward boundaries, Cadastral Parcels, 3D Floor Units,
LiDAR Point Clouds, and ULPIN requests.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class FloorUnit(BaseModel):
    floor_index: int
    floor_label: str
    z_min: float
    z_max: float
    height: float
    area_sqm: float
    ulpin_3d: str
    unit_type: str
    unit_id: str


class Building3D(BaseModel):
    building_id: str
    building_name: str
    floors_count: int
    height_m: float
    base_elevation_m: float
    roof_elevation_m: float
    footprint_area_sqm: float
    built_up_area_sqm: float
    full_mesh: Optional[Dict[str, Any]] = None
    floors: List[FloorUnit] = []
    floor_meshes: Optional[List[Dict[str, Any]]] = None


class ExtrusionData(BaseModel):
    parcel_id: str
    parcel_ulpin: str
    land_use: str
    owner_name: str
    parcel_area_sqm: float
    base_elevation_m: float
    max_height_m: float
    far: float
    buildings_count: int
    total_units_count: int
    buildings: List[Building3D] = []
    origin_utm: List[float] = []


class Parcel(BaseModel):
    parcel_id: str
    ward_id: str
    ulpin: str
    survey_number: str
    land_use: str
    owner_name: str
    area_sqm: float
    centroid: Dict[str, float]
    geometry: Dict[str, Any]
    buildings_count: int = 1
    floors_count: int = 1
    extrusion: Optional[ExtrusionData] = None


class Ward(BaseModel):
    id: str
    name: str
    properties: Dict[str, Any]
    geometry: Dict[str, Any]
    bbox: List[float]
    parcels_count: int = 0


class ULPINEncodeRequest(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0, description="WGS84 Latitude")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="WGS84 Longitude")
    floor: int = Field(default=0, ge=-20, le=200, description="Floor level (0 for ground)")


class ULPINDecodeRequest(BaseModel):
    ulpin: str = Field(..., min_length=14, max_length=14, description="14-character ULPIN code")


class StatsResponse(BaseModel):
    total_wards: int
    active_wards_with_parcels: int
    total_parcels: int
    total_buildings: int
    total_3d_units: int
    total_land_area_sqm: float
    total_built_up_area_sqm: float
    land_use_breakdown: Dict[str, int]
