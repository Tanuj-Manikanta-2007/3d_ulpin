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
    check_3d_vertical_overlap,
    validate_3d_cadastral_topology,
    geojson_to_shapely,
    get_bbox_wgs84,
    shapely_to_geojson
)
from backend.services.ai_extractor import extract_building_footprints_from_image
from backend.services.lidar_pipeline import lidar_pipeline_instance
from backend.services.floorplan_vectorizer import floorplan_vectorizer_instance
from backend.services.elevation_service import elevation_service_instance



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
    count: Optional[int] = Query(None, description="Target number of parcels (defaults to all possible in ward)"),
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


@app.post("/api/upload/lidar")
async def upload_drone_lidar(
    file: Optional[UploadFile] = File(None),
    parcel_id: Optional[str] = Query(None),
    min_lon: float = Query(78.379),
    min_lat: float = Query(17.439),
    max_lon: float = Query(78.383),
    max_lat: float = Query(17.443)
):
    """
    Branch A: Drone / LiDAR Upload (.laz/.las) + Orthophoto.
    Executes nDSM generation, semantic point cloud segmentation, vertical floor slicing [Z_min, Z_max],
    and regularized 2D building footprint extraction.
    """
    try:
        contents = await file.read() if file else b""
        filename = file.filename if file else "sample_flight.laz"

        # 1. Parse point cloud (real laspy or CORS-corrected synthetic scan)
        pc = lidar_pipeline_instance.parse_point_cloud(contents, filename)

        # 2. Segment into Ground, Roof, and Facades
        segmented = lidar_pipeline_instance.segment_point_cloud(pc)

        # 3. Associate or derive Base ULPIN
        parcel = db_instance.get_parcel(parcel_id) if parcel_id else None
        base_ulpin = parcel["ulpin"] if parcel else generate_prototype_ulpin((min_lat + max_lat) / 2.0, (min_lon + max_lon) / 2.0, 0)

        # 4. Vertical Facade Floor Slicing
        floors = lidar_pipeline_instance.slice_vertical_floors(segmented, base_ulpin)

        # 5. Regularized Building Footprint
        footprint = lidar_pipeline_instance.extract_regularized_footprint(pc, [min_lon, min_lat, max_lon, max_lat])

        return {
            "status": "success",
            "branch": "Branch_A_LiDAR_Drone",
            "filename": filename,
            "points_processed": pc["point_count"],
            "ground_elevation_msl": segmented["ground_elevation_msl"],
            "max_elevation_msl": segmented["max_elevation_msl"],
            "building_height_m": segmented["ndsm_height_m"],
            "floors_count": len(floors),
            "floors": floors,
            "footprint": footprint,
            "base_ulpin": base_ulpin[:14]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LiDAR processing error: {str(e)}")


@app.post("/api/upload/floorplan")
async def upload_architectural_floorplan(
    file: Optional[UploadFile] = File(None),
    parcel_id: Optional[str] = Query(None),
    floor_index: int = Query(1, ge=0, le=50),
    total_levels: int = Query(4, ge=1, le=50),
    floor_height_m: float = Query(3.2, ge=2.4, le=6.0)
):
    """
    Branch B: Upload Architectural Floor Plan (PDF / Image) + Levels input.
    Extracts room/apartment vectors and calculates sub-unit areas.
    """
    try:
        contents = await file.read() if file else b""
        filename = file.filename if file else "architectural_plan.pdf"

        parcel = db_instance.get_parcel(parcel_id) if parcel_id else None
        bldg_poly = geojson_to_shapely(parcel["geometry"]) if parcel else None

        vectorized = floorplan_vectorizer_instance.vectorize_floorplan(
            file_bytes=contents,
            filename=filename,
            building_polygon_wgs84=bldg_poly,
            floor_index=floor_index
        )

        base_ulpin = parcel["ulpin"] if parcel else "832454DYJFAQY2"
        base_14 = base_ulpin[:14]

        # Attach 18-character 3D ULPIN to each room/apartment unit
        for u in vectorized["units"]:
            suffix = f"-F{floor_index:02d}"
            u["ulpin_3d"] = f"{base_14}{suffix}"

        return {
            "status": "success",
            "branch": "Branch_B_FloorPlan_Vectorizer",
            "total_levels": total_levels,
            "floor_height_m": floor_height_m,
            "estimated_building_height_m": round(total_levels * floor_height_m, 2),
            "floorplan": vectorized
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Floor plan vectorization failed: {str(e)}")


@app.get("/api/elevation/opentopography")
def get_elevation_opentopography(
    lat: float = Query(17.4400),
    lon: float = Query(78.3800),
    dem_type: str = Query("COP30", description="Copernicus 30m, SRTMGL1, or AW3D30")
):
    """Fetch baseline ground elevation from OpenTopography API or Regional Copernicus 30m model."""
    elev_data = elevation_service_instance.get_baseline_elevation(lat, lon, dem_type)
    return elev_data


@app.post("/api/topology/validate-3d")
def validate_3d_topology(
    parcel_id: str = Query(..., description="Parcel ID to validate")
):
    """
    PostGIS / Shapely 3D Cadastral Topology Validation Engine:
    - Encroachment check vs legal parcel boundary.
    - No vertical overlapping between floor strata and neighboring volumetric bounds.
    - Clear Deed / Registration authorization status.
    """
    parcel = db_instance.get_parcel(parcel_id)
    if not parcel:
        raise HTTPException(status_code=404, detail=f"Parcel {parcel_id} not found")

    parcel_poly = geojson_to_shapely(parcel["geometry"])
    ext = parcel.get("extrusion") or {}
    buildings = ext.get("buildings", [])

    if buildings:
        main_b = buildings[0]
        bldg_poly = geojson_to_shapely(main_b["floors"][0].get("geometry", parcel["geometry"]))
        floors = main_b.get("floors", [])
    else:
        bldg_poly = parcel_poly.buffer(-0.00003)
        floors = [{
            "floor_index": 0, "floor_label": "L00 (Ground)",
            "z_min": ext.get("base_elevation_m", 512.0),
            "z_max": ext.get("base_elevation_m", 512.0) + 3.2
        }]

    report = validate_3d_cadastral_topology(
        parcel_wgs84=parcel_poly,
        building_wgs84=bldg_poly,
        floors=floors,
        parcel_ulpin=parcel["ulpin"]
    )
    report["parcel_id"] = parcel_id
    report["survey_number"] = parcel.get("survey_number")
    report["owner_name"] = parcel.get("owner_name")
    return report


@app.get("/api/tiles3d/{parcel_id}")
def get_3d_tiles_layer(parcel_id: str = Path(..., description="Parcel ID")):
    """
    Publish 3D Layer formatted for CesiumJS & Three.js 3D Digital Twin Viewer.
    Includes extruded polyhedra, floor slices, and compliance classification.
    """
    parcel = db_instance.get_parcel(parcel_id)
    if not parcel:
        raise HTTPException(status_code=404, detail=f"Parcel {parcel_id} not found")

    ext = parcel.get("extrusion") or {}
    base_elev = ext.get("base_elevation_m", 512.0)
    buildings = ext.get("buildings", [])

    features_3d = []
    if buildings:
        for fl in buildings[0].get("floors", []):
            features_3d.append({
                "type": "Feature",
                "properties": {
                    "floor_label": fl.get("floor_label"),
                    "floor_index": fl.get("floor_index"),
                    "z_min": fl.get("z_min"),
                    "z_max": fl.get("z_max"),
                    "ulpin_3d": fl.get("ulpin_3d"),
                    "unit_type": fl.get("unit_type", "Apartment"),
                    "color": "#00f2fe" if fl.get("floor_index", 0) % 2 == 0 else "#3b82f6"
                },
                "geometry": parcel["geometry"]
            })

    return {
        "type": "FeatureCollection",
        "parcel_id": parcel_id,
        "base_elevation_msl": base_elev,
        "datum": "EGM96 / WGS84",
        "viewer_target": "CesiumJS_and_ThreeJS",
        "features": features_3d
    }


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
