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
from fastapi.responses import FileResponse, JSONResponse

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
    return {
        "status": "online",
        "service": "3D ULPIN Cadastral Engine",
        "total_wards": len(db_instance.wards),
        "total_parcels": len(db_instance.parcels),
        "integrations": {
            "mapbox": bool(os.getenv("MAPBOX_ACCESS_TOKEN")),
            "opentopography": bool(os.getenv("OPENTOPOGRAPHY_API_KEY"))
        }
    }


@app.get("/api/wards")
def list_wards():
    """List all 145 Hyderabad administrative wards with their parcel counts."""
    return {"wards": db_instance.get_all_wards()}


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
    count: int = Query(18, ge=4, le=60, description="Target number of parcels to generate"),
    source: str = Query("osm", description="Data source: 'osm' (Live OpenStreetMap) or 'synthetic' (Voronoi partitioning)")
):
    """Generate parcels, 3D building extrusions, and 3D ULPINs for a selected ward from OSM or Synthetic generator."""
    try:
        parcels = db_instance.generate_ward_parcels(ward_id, target_parcels=count, source=source)
        return {
            "message": f"Successfully generated {len(parcels)} parcels for Ward {ward_id} from source: {source}",
            "ward_id": ward_id,
            "source": source,
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
    search: Optional[str] = Query(None, description="Search term (ULPIN, Owner, Survey No)")
):
    """List parcels with filtering and GeoJSON representation."""
    parcels_list = db_instance.get_parcels(ward_id=ward_id, land_use=land_use, search=search)
    
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


@app.get("/api/parcels/{parcel_id}/3d")
def get_parcel_3d(parcel_id: str = Path(..., description="Parcel ID or 14-char ULPIN")):
    """Get 3D extruded mesh, floor units, and elevation profile for 3D visualization."""
    parcel = db_instance.get_parcel(parcel_id)
    if not parcel:
        raise HTTPException(status_code=404, detail=f"Parcel {parcel_id} not found")
    return parcel.get("extrusion", {})


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

    @app.get("/")
    def serve_frontend_index():
        return FileResponse(os.path.join(frontend_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
