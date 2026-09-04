"""
spatial_service.py
Spatial calculations and Coordinate Reference System (CRS) transformations
using Shapely and pyproj.

CRS Reference:
- EPSG:4326: WGS84 (Longitude, Latitude in degrees per RFC 7946)
- EPSG:32644: UTM Zone 44N (Metric coordinates X, Y in metres) - Hyderabad
"""

from typing import List, Tuple, Dict, Any, Union
from shapely.geometry import Polygon, MultiPolygon, Point, mapping, shape
from shapely.ops import transform
from shapely.validation import make_valid
import pyproj


# Pre-configured pyproj transformers
transformer_4326_to_32644 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)
transformer_32644_to_4326 = pyproj.Transformer.from_crs("EPSG:32644", "EPSG:4326", always_xy=True)


def normalize_coords(coords: Any) -> Any:
    """
    Ensure coordinates follow standard GeoJSON RFC 7946 [longitude, latitude, elevation].
    Detects if coordinates are mistakenly given as [latitude, longitude] and swaps them.
    """
    if isinstance(coords, (list, tuple)) and len(coords) >= 2 and isinstance(coords[0], (int, float)):
        # If first value is Latitude (~17) and second is Longitude (~78)
        if coords[0] < 30.0 and coords[1] > 60.0:
            return [coords[1], coords[0]] + list(coords[2:])
        return list(coords)
    elif isinstance(coords, (list, tuple)):
        return [normalize_coords(c) for c in coords]
    return coords


def normalize_geojson_geometry(geom_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize any GeoJSON geometry dict to RFC 7946 [lon, lat] order."""
    if not geom_dict or "coordinates" not in geom_dict:
        return geom_dict
    normalized_coords = normalize_coords(geom_dict["coordinates"])
    return {
        "type": geom_dict.get("type", "Polygon"),
        "coordinates": normalized_coords
    }


def to_utm(geometry: Union[Polygon, MultiPolygon, Point]) -> Union[Polygon, MultiPolygon, Point]:
    """Transform a Shapely geometry from WGS84 (lon, lat) to UTM Zone 44N (metric X, Y)."""
    return transform(transformer_4326_to_32644.transform, geometry)


def to_wgs84(geometry: Union[Polygon, MultiPolygon, Point]) -> Union[Polygon, MultiPolygon, Point]:
    """Transform a Shapely geometry from UTM Zone 44N (metric X, Y) to WGS84 (lon, lat)."""
    return transform(transformer_32644_to_4326.transform, geometry)


def reproject_point_to_utm(lon: float, lat: float) -> Tuple[float, float]:
    """Convert (lon, lat) to (x_utm, y_utm)."""
    x, y = transformer_4326_to_32644.transform(lon, lat)
    return x, y


def reproject_point_to_wgs84(x_utm: float, y_utm: float) -> Tuple[float, float]:
    """Convert (x_utm, y_utm) to (lon, lat)."""
    lon, lat = transformer_32644_to_4326.transform(x_utm, y_utm)
    return lon, lat


def validate_and_fix_polygon(poly: Polygon) -> Polygon:
    """Validate a polygon and fix self-intersections or orientation issues."""
    if not poly.is_valid:
        fixed = make_valid(poly)
        if isinstance(fixed, MultiPolygon):
            return max(fixed.geoms, key=lambda g: g.area)
        elif isinstance(fixed, Polygon):
            return fixed
    return poly


def calculate_metric_area(polygon_wgs84: Polygon) -> float:
    """Calculate the surface area of a WGS84 (lon, lat) polygon in square metres."""
    poly_utm = to_utm(polygon_wgs84)
    return float(poly_utm.area)


def calculate_metric_perimeter(polygon_wgs84: Polygon) -> float:
    """Calculate perimeter in metres."""
    poly_utm = to_utm(polygon_wgs84)
    return float(poly_utm.length)


def get_centroid_wgs84(polygon_wgs84: Polygon) -> Tuple[float, float]:
    """Get representative interior point in (latitude, longitude)."""
    pt = polygon_wgs84.representative_point()
    # pt.x is Longitude, pt.y is Latitude
    return float(pt.y), float(pt.x)  # lat, lon


def get_bbox_wgs84(polygon_wgs84: Polygon) -> List[float]:
    """Return [min_lon, min_lat, max_lon, max_lat]."""
    return list(polygon_wgs84.bounds)


def geojson_to_shapely(geometry_dict: Dict[str, Any]) -> Union[Polygon, MultiPolygon]:
    """Convert a GeoJSON geometry dict to a validated Shapely geometry (normalized to [lon, lat])."""
    normalized_dict = normalize_geojson_geometry(geometry_dict)
    geom = shape(normalized_dict)
    if isinstance(geom, Polygon):
        return validate_and_fix_polygon(geom)
    elif isinstance(geom, MultiPolygon):
        return geom
    raise ValueError(f"Unsupported geometry type: {geom.geom_type}")


def shapely_to_geojson(geom: Union[Polygon, MultiPolygon, Point]) -> Dict[str, Any]:
    """Convert a Shapely geometry to a standard GeoJSON mapping."""
    return mapping(geom)


def check_building_encroachment(
    parcel_wgs84: Polygon,
    building_wgs84: Polygon
) -> Dict[str, Any]:
    """
    Compare building footprint against legal parcel polygon using Shapely in UTM 44N metric space.
    Calculates:
      - parcel_area_sqm
      - building_area_sqm
      - intersection_area_sqm (building area inside parcel)
      - encroached_area_sqm (building area outside parcel)
      - encroachment_percent (% of building outside legal parcel)
      - status: 'ENCROACHED' or 'CLEAN'
    """
    parcel_utm = validate_and_fix_polygon(to_utm(parcel_wgs84))
    building_utm = validate_and_fix_polygon(to_utm(building_wgs84))

    parcel_area = float(parcel_utm.area)
    building_area = float(building_utm.area)

    intersection = parcel_utm.intersection(building_utm)
    intersection_area = float(intersection.area) if not intersection.is_empty else 0.0

    encroached_area = max(0.0, building_area - intersection_area)
    encroachment_percent = (encroached_area / building_area * 100.0) if building_area > 0 else 0.0

    # Consider encroached if more than 0.5 sqm or > 1% is outside parcel
    is_encroached = encroached_area > 0.5 and encroachment_percent > 1.0

    return {
        "parcel_area_sqm": round(parcel_area, 2),
        "building_area_sqm": round(building_area, 2),
        "intersection_area_sqm": round(intersection_area, 2),
        "encroached_area_sqm": round(encroached_area, 2),
        "encroachment_percent": round(encroachment_percent, 2),
        "is_encroached": is_encroached,
        "status": "ENCROACHED" if is_encroached else "CLEAN"
    }

