"""
main.py
FastAPI REST API application for 3D ULPIN (Unique Land Parcel Identification Number) System.
Exposes spatial queries, 3D mesh retrieval, LiDAR point cloud generation,
DoLR/NIC ULPIN encoding/decoding, OpenStreetMap live querying, and static frontend assets.
"""

import os
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Path, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response

from backend.database.db import db_instance
from backend.database.models import (
    ULPINEncodeRequest,
    ULPINDecodeRequest,
    StatsResponse
)
from backend.services.ulpin_generator import (
    generate_prototype_ulpin,
    decode_prototype_ulpin,
    get_ulpin_breakdown
)
from backend.services.spatial_service import (
    check_building_encroachment,
    geojson_to_shapely,
    get_bbox_wgs84
)
from backend.services.ai_extractor import extract_building_footprints_from_image



app = FastAPI(
    title="3D ULPIN (Unique Land Parcel Identification Number) Geospatial API",
    description="3D Cadastral and Land Information System for Hyderabad Wards",
    version="1.0.0"
)

# Enable CORS for interactive web mapping
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/config")
def get_client_config():
    """Returns public client configuration and available external service integrations."""
    mapbox_token = os.getenv("MAPBOX_ACCESS_TOKEN", "").strip()
    opentopography_key = os.getenv("OPENTOPOGRAPHY_API_KEY", "").strip()
    return {
        "mapbox_token": mapbox_token if mapbox_token else None,
        "has_mapbox": bool(mapbox_token),
        "has_opentopography": bool(opentopography_key),
        "environment": os.getenv("ENVIRONMENT", "development")
    }


@app.get("/api/health")
def health_check():
    stats = db_instance.get_stats()
    return {
        "status": "online",
        "service": "3D ULPIN Cadastral Engine",
        "database": "SQLite" if db_instance.is_sqlite else "PostgreSQL",
        "total_wards": stats["total_wards"],
        "total_parcels": stats["total_parcels"],
        "integrations": {
            "mapbox": bool(os.getenv("MAPBOX_ACCESS_TOKEN")),
            "opentopography": bool(os.getenv("OPENTOPOGRAPHY_API_KEY"))
        }
    }


@app.get("/api/states")
def list_states():
    """List all supported states with city and ward counts."""
    return {"states": db_instance.get_all_states()}


@app.get("/api/states/{state_code}/cities")
def list_cities(state_code: str = Path(..., description="State code, e.g. TS, KA, MH, DL")):
    """List all cities/ULBs for a specific state."""
    return {"cities": db_instance.get_cities_by_state(state_code)}


@app.get("/api/cities/{city_id}/wards")
def list_city_wards(city_id: str = Path(..., description="City ID, e.g. TS-HYD, KA-BLR")):
    """List all wards for a specific city/ULB."""
    return {"wards": db_instance.get_wards_by_city(city_id)}


@app.get("/api/wards")
def list_wards(city_id: Optional[str] = Query(None, description="Filter wards by city ID")):
    """List administrative wards with their parcel counts."""
    return {"wards": db_instance.get_all_wards(city_id=city_id)}


@app.get("/api/wards/{ward_id}")
def get_ward(ward_id: str = Path(..., description="Ward ID")):
    """Get single ward details and boundary geometry."""
    w = db_instance.get_ward(ward_id)
    if not w:
        raise HTTPException(status_code=404, detail=f"Ward ID {ward_id} not found")
    return w


@app.post("/api/wards/{ward_id}/generate")
def generate_ward_parcels(
    ward_id: str = Path(..., description="Ward ID"),
<<<<<<< HEAD
    count: Optional[int] = Query(None, description="Optional limit on target number of parcels to generate"),
=======
    count: Optional[int] = Query(None, description="Target number of parcels (defaults to all possible in ward)"),
>>>>>>> d674a2a5c7876f346ceac71627e5a12456fc5451
    source: str = Query("osm", description="Data source: 'osm' (Live OpenStreetMap) or 'synthetic' (Voronoi partitioning)")
):
    """Generate all possible parcels in the ward location, storing 50 parcels directly into PostgreSQL."""
    try:
        parcels = db_instance.generate_ward_parcels(ward_id, target_parcels=count, source=source, max_db_parcels=50)
        total_detected = parcels[0].get("total_detected_in_ward", len(parcels)) if parcels else len(parcels)
        db_count = sum(1 for p in parcels if p.get("is_persisted_to_db")) or min(len(parcels), 50)
        return {
            "message": f"Generated all {len(parcels)} parcels for Ward {ward_id} ({db_count} stored in database).",
            "ward_id": ward_id,
            "source": source,
            "total_generated": len(parcels),
            "stored_in_db": db_count,
            "total_detected": total_detected,
            "parcels_count": len(parcels),
            "parcels": parcels
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation error: {str(e)}")


@app.get("/api/parcels")
def list_parcels(
    ward_id: Optional[str] = Query(None, description="Filter by ward ID"),
    land_use: Optional[str] = Query(None, description="Filter by land use"),
    search: Optional[str] = Query(None, description="Search term (ULPIN, Owner, Survey No)"),
    bbox: Optional[str] = Query(None, description="Bounding box min_lon,min_lat,max_lon,max_lat"),
    source: str = Query("osm", description="Generation mode if ward is not in DB: 'osm' or 'synthetic'"),
    limit: Optional[int] = Query(None, description="Max parcels to return (defaults to all available)")
):
    """List parcels with filtering and GeoJSON representation. Uses DB-first caching."""
    bbox_coords = None
    if bbox:
        try:
            parts = [float(x.strip()) for x in bbox.split(",")]
            if len(parts) == 4:
                bbox_coords = parts
        except Exception:
            bbox_coords = None

    lu_str = land_use if isinstance(land_use, str) else None
    search_str = search if isinstance(search, str) else None
    source_str = source if isinstance(source, str) else "osm"

    parcels_list = db_instance.get_parcels(
        ward_id=ward_id if isinstance(ward_id, str) else None,
        land_use=lu_str,
        search=search_str,
        bbox=bbox_coords,
        source=source_str,
        limit=limit
    )
    
    # Format as GeoJSON FeatureCollection
    features = []
    for p in parcels_list:
        features.append({
            "type": "Feature",
            "id": p["parcel_id"],
            "properties": {
                "parcel_id": p["parcel_id"],
                "ward_id": p["ward_id"],
                "ulpin": p["ulpin"],
                "survey_number": p["survey_number"],
                "land_use": p["land_use"],
                "owner_name": p["owner_name"],
                "area_sqm": p["area_sqm"],
                "buildings_count": p["buildings_count"],
                "floors_count": p["floors_count"],
                "data_source": p.get("data_source", "Synthetic"),
                "is_persisted_to_db": p.get("is_persisted_to_db", True),
                "centroid": p["centroid"]
            },
            "geometry": p["geometry"]
        })
        
    return {
        "type": "FeatureCollection",
        "count": len(parcels_list),
        "features": features
    }


@app.get("/api/parcels/{parcel_id}")
def get_parcel(parcel_id: str = Path(..., description="Parcel ID or 14-char ULPIN")):
    """Get full details of a parcel."""
    parcel = db_instance.get_parcel(parcel_id)
    if not parcel:
        raise HTTPException(status_code=404, detail=f"Parcel {parcel_id} not found")
    return parcel


from backend.services.extrusion_engine import extrude_parcel_and_buildings


@app.get("/api/parcels/{parcel_id}/3d")
def get_parcel_3d(parcel_id: str = Path(..., description="Parcel ID or 14-char ULPIN")):
    """Get 3D extruded mesh, floor units, and elevation profile for 3D visualization."""
    parcel = db_instance.get_parcel(parcel_id)
    if not parcel:
        raise HTTPException(status_code=404, detail=f"Parcel {parcel_id} not found")
<<<<<<< HEAD

    ext = parcel.get("extrusion")
    if not ext:
        poly_wgs84 = geojson_to_shapely(parcel["geometry"])
        floors = parcel.get("floors_count", 4)
        ext = extrude_parcel_and_buildings(
            parcel_wgs84=poly_wgs84,
            buildings_wgs84=[{
                "geometry": poly_wgs84.buffer(-0.00003),
                "floors": floors,
                "floor_height": 3.2,
                "name": f"Building {parcel['parcel_id']}"
            }],
            parcel_ulpin=parcel["ulpin"],
            parcel_id=parcel["parcel_id"],
            land_use=parcel.get("land_use", "Residential"),
            owner_name=parcel.get("owner_name", "Owner")
        )
        parcel["extrusion"] = ext
    return ext
=======
    return parcel.get("extrusion") or {}
>>>>>>> d674a2a5c7876f346ceac71627e5a12456fc5451


@app.get("/api/parcels/{parcel_id}/lidar")
def get_parcel_lidar(parcel_id: str = Path(..., description="Parcel ID or 14-char ULPIN")):
    """Get synthetic 3D LiDAR point cloud points (XYZ, intensity, classification)."""
    try:
        points = db_instance.get_parcel_lidar(parcel_id)
        return {
            "parcel_id": parcel_id,
            "points_count": len(points),
            "points": points
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/parcels/{parcel_id}/encroachment")
def get_parcel_encroachment(parcel_id: str = Path(..., description="Parcel ID or 14-char ULPIN")):
    """Check 2D building footprint vs legal parcel boundary encroachment using Shapely in UTM 44N."""
    parcel = db_instance.get_parcel(parcel_id)
    if not parcel:
        raise HTTPException(status_code=404, detail=f"Parcel {parcel_id} not found")

    parcel_poly = geojson_to_shapely(parcel["geometry"])
    extrusion = parcel.get("extrusion", {})
    buildings = extrusion.get("buildings", [])

    if not buildings:
        return {
            "parcel_id": parcel_id,
            "status": "CLEAN",
            "is_encroached": False,
            "encroached_area_sqm": 0.0,
            "encroachment_percent": 0.0,
            "message": "No building structures recorded on parcel."
        }

    # Compare first/main building footprint against parcel boundary
    bldg_poly = geojson_to_shapely(buildings[0]["floors"][0].get("geometry", parcel["geometry"]))
    analysis = check_building_encroachment(parcel_poly, bldg_poly)
    analysis["parcel_id"] = parcel_id
    analysis["ulpin"] = parcel.get("ulpin")
    return analysis


@app.get("/api/parcels/{parcel_id}/underground")
def get_parcel_underground(parcel_id: str = Path(..., description="Parcel ID or 14-char ULPIN")):
    """Get sub-surface 3D volumetric units (Basement parking B01/B02, Utility conduits)."""
    parcel = db_instance.get_parcel(parcel_id)
    if not parcel:
        raise HTTPException(status_code=404, detail=f"Parcel {parcel_id} not found")
    extrusion = parcel.get("extrusion", {})
    underground = extrusion.get("underground_units", [])
    return {
        "parcel_id": parcel_id,
        "ulpin": parcel.get("ulpin"),
        "surface_ulpin": extrusion.get("surface_ulpin"),
        "underground_units_count": len(underground),
        "underground_units": underground
    }


@app.post("/api/ai/extract-footprints")
def ai_extract_footprints(
    file: Optional[UploadFile] = File(None),
    min_lon: float = Query(78.375),
    min_lat: float = Query(17.435),
    max_lon: float = Query(78.385),
    max_lat: float = Query(17.445)
):
    """AI/ML Building Footprint Extraction from drone/satellite imagery."""
    try:
        bbox = [min_lon, min_lat, max_lon, max_lat]
        if file:
            contents = file.file.read()
            extracted = extract_building_footprints_from_image(contents, bbox)
        else:
            extracted = extract_building_footprints_from_image(b"", bbox)

        return {
            "status": "success",
            "source": "AI_Segmentation_Module",
            "bbox_wgs84": bbox,
            "extracted_count": len(extracted),
            "footprints": extracted
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI extraction failed: {str(e)}")



@app.post("/api/ulpin/encode")
def encode_ulpin(req: ULPINEncodeRequest):
    """Encode latitude, longitude, and floor into a DoLR/NIC 14-char ULPIN with computational steps."""
    try:
        breakdown = get_ulpin_breakdown(req.latitude, req.longitude, req.floor)
        return breakdown
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/ulpin/decode")
def decode_ulpin(req: ULPINDecodeRequest):
    """Decode a 14-char ULPIN back to latitude, longitude, and floor level."""
    try:
        result = decode_prototype_ulpin(req.ulpin)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/stats")
def get_stats() -> StatsResponse:
    """Get system-wide summary metrics."""
    return db_instance.get_stats()


# Mount frontend static directory
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get("/.well-known/{rest_of_path:path}", include_in_schema=False)
    def devtools_well_known(rest_of_path: str):
        return Response(status_code=204)

    @app.get("/")
    def serve_frontend_index():
        return FileResponse(os.path.join(frontend_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
