"""
db.py
Database layer for 3D ULPIN Cadastral System.
Supports both PostgreSQL (+ PostGIS) and persistent local SQLite (data/cadastre.db).
Implements multi-state, multi-city hierarchical storage:
  State -> City/ULB -> Ward -> Cadastral Parcels -> 3D Floor Units.

Key features:
- Persistent storage across server restarts.
- DB-first caching: If parcels exist in the database, return them immediately.
  If not yet in the database, generate on-demand via OSM / synthetic cadastre and persist into DB.
- Comprehensive seed data for Hyderabad (GHMC - 145 wards from GeoJSON),
  Bengaluru (BBMP), Mumbai (BMC), and New Delhi (MCD).
"""

import os
import json
import threading
from urllib.parse import quote, unquote
from typing import List, Dict, Any, Optional
from shapely.geometry import Polygon, MultiPolygon, box
from sqlalchemy import create_engine, event, func
from sqlalchemy.orm import sessionmaker, scoped_session
from dotenv import load_dotenv

load_dotenv()

from backend.database.schema import (
    Base,
    StateTable,
    CityTable,
    WardTable,
    ParcelTable,
    BuildingTable,
    FloorUnitTable,
    LiDARCacheTable
)
from backend.services.spatial_service import (
    validate_and_fix_polygon,
    get_bbox_wgs84,
    geojson_to_shapely,
    shapely_to_geojson
)
from backend.services.dataset_generator import (
    partition_ward_into_parcels,
    generate_parcels_from_osm
)
from backend.services.extrusion_engine import extrude_parcel_and_buildings
from backend.services.lidar_service import generate_synthetic_lidar_points


class SpatialDatabase:
    def __init__(self, db_url: Optional[str] = None):
        self._lock = threading.RLock()
        
        # Base directory
        self.base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        data_dir = os.path.join(self.base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)

        # Database URL: PostgreSQL if configured, otherwise local SQLite
        if db_url is None:
            db_url = os.getenv("DATABASE_URL", "").strip()
        
        self.is_sqlite = True
        sqlite_path = os.path.join(data_dir, "cadastre.db").replace("\\", "/")
        self.db_url = f"sqlite:///{sqlite_path}"

        if db_url and not db_url.startswith("sqlite"):
            # Fix postgres:// to postgresql:// if needed
            if db_url.startswith("postgres://"):
                db_url = db_url.replace("postgres://", "postgresql://", 1)
            # Safe URL encoding of username/password
            if "://" in db_url and "@" in db_url:
                scheme, remainder = db_url.split("://", 1)
                credentials, host = remainder.rsplit("@", 1)
                if ":" in credentials:
                    username, password = credentials.split(":", 1)
                    db_url = f"{scheme}://{quote(unquote(username), safe='')}:{quote(unquote(password), safe='')}@{host}"
            
            try:
                pg_engine = create_engine(
                    db_url,
                    pool_size=5,
                    max_overflow=10,
                    pool_recycle=60,
                    pool_pre_ping=True,
                    connect_args={
                        "connect_timeout": 10,
                        "keepalives": 1,
                        "keepalives_idle": 30,
                        "keepalives_interval": 10,
                        "keepalives_count": 5
                    },
                    echo=False
                )
                # Test connection and initialize tables
                Base.metadata.create_all(bind=pg_engine)
                self.engine = pg_engine
                self.db_url = db_url
                self.is_sqlite = False
                print(f"[Database] Successfully connected to PostgreSQL (Supabase): {self.db_url.split('@')[-1]}")
            except Exception as e:
                print(f"[Database Warning] PostgreSQL connection failed: {e}")
                print(f"[Database] Falling back to local persistent SQLite at {sqlite_path}")
                self.is_sqlite = True

        if self.is_sqlite:
            self.engine = create_engine(
                self.db_url,
                connect_args={"check_same_thread": False},
                echo=False
            )
            @event.listens_for(self.engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()
            Base.metadata.create_all(bind=self.engine)
            print(f"[Database] Connected to SQLite: {self.db_url}")

        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.ScopedSession = scoped_session(self.session_factory)

        # In-memory fast caches
        self._parcels_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._lidar_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._parcels_lookup: Dict[str, Dict[str, Any]] = {}
        self._wards_cache: Dict[str, Dict[str, Any]] = {}

        # Seed initial data
        self.seed_initial_data()

    def get_session(self):
        return self.ScopedSession()

    def seed_initial_data(self):
        """Seed states, cities, wards, and default flagship parcels if empty."""
        with self._lock:
            session = self.get_session()
            try:
                # 1. Seed States if not present
                if session.query(StateTable).count() == 0:
                    states_data = [
                        StateTable(
                            state_code="TS",
                            state_name="Telangana",
                            country_code="IN",
                            center_lat=17.3850,
                            center_lon=78.4867,
                            min_lon=77.2, min_lat=15.8, max_lon=81.8, max_lat=19.9
                        ),
                        StateTable(
                            state_code="KA",
                            state_name="Karnataka",
                            country_code="IN",
                            center_lat=12.9716,
                            center_lon=77.5946,
                            min_lon=74.0, min_lat=11.5, max_lon=78.6, max_lat=18.5
                        ),
                        StateTable(
                            state_code="MH",
                            state_name="Maharashtra",
                            country_code="IN",
                            center_lat=19.0760,
                            center_lon=72.8777,
                            min_lon=72.5, min_lat=15.6, max_lon=80.9, max_lat=22.0
                        ),
                        StateTable(
                            state_code="DL",
                            state_name="Delhi NCT",
                            country_code="IN",
                            center_lat=28.6139,
                            center_lon=77.2090,
                            min_lon=76.8, min_lat=28.4, max_lon=77.4, max_lat=28.9
                        )
                    ]
                    session.add_all(states_data)
                    session.commit()
                    print("[Database] Seeded 4 Flagship States: TS, KA, MH, DL")

                # 2. Seed Cities if not present
                if session.query(CityTable).count() == 0:
                    cities_data = [
                        CityTable(
                            city_id="TS-HYD",
                            state_code="TS",
                            city_name="Hyderabad",
                            ulb_type="Greater Hyderabad Municipal Corporation (GHMC)",
                            center_lat=17.4400,
                            center_lon=78.3800,
                            min_lon=78.2, min_lat=17.2, max_lon=78.6, max_lat=17.6
                        ),
                        CityTable(
                            city_id="KA-BLR",
                            state_code="KA",
                            city_name="Bengaluru",
                            ulb_type="Bruhat Bengaluru Mahanagara Palike (BBMP)",
                            center_lat=12.9716,
                            center_lon=77.5946,
                            min_lon=77.4, min_lat=12.8, max_lon=77.8, max_lat=13.1
                        ),
                        CityTable(
                            city_id="MH-MUM",
                            state_code="MH",
                            city_name="Mumbai",
                            ulb_type="Brihanmumbai Municipal Corporation (BMC)",
                            center_lat=19.0760,
                            center_lon=72.8777,
                            min_lon=72.75, min_lat=18.88, max_lon=73.0, max_lat=19.3
                        ),
                        CityTable(
                            city_id="DL-DEL",
                            state_code="DL",
                            city_name="New Delhi",
                            ulb_type="Municipal Corporation of Delhi (MCD)",
                            center_lat=28.6139,
                            center_lon=77.2090,
                            min_lon=77.0, min_lat=28.45, max_lon=77.35, max_lat=28.85
                        )
                    ]
                    session.add_all(cities_data)
                    session.commit()
                    print("[Database] Seeded 4 Flagship Municipal Corporations (GHMC, BBMP, BMC, MCD)")

                # 3. Seed Wards if not present
                if session.query(WardTable).count() == 0:
                    # A. Load all 145 Hyderabad wards from GeoJSON
                    geojson_path = os.path.join(self.base_dir, "data", "wards_hyderabad.geojson")
                    if os.path.exists(geojson_path):
                        with open(geojson_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        features = data.get("features", [])
                        hyd_wards = []
                        for idx, feat in enumerate(features):
                            ward_id = str(feat.get("id", idx))
                            props = feat.get("properties", {})
                            name = props.get("Name", f"Ward {ward_id}")
                            raw_geom_dict = feat.get("geometry", {})
                            try:
                                sh_geom = geojson_to_shapely(raw_geom_dict)
                                norm_geom_dict = shapely_to_geojson(sh_geom)
                                bbox = list(sh_geom.bounds)
                                centroid = sh_geom.centroid
                                c_lon, c_lat = centroid.x, centroid.y
                            except Exception:
                                norm_geom_dict = raw_geom_dict
                                bbox = [78.2, 17.2, 78.6, 17.6]
                                c_lon, c_lat = 78.38, 17.44

                            hyd_wards.append(WardTable(
                                ward_id=ward_id,
                                city_id="TS-HYD",
                                state_code="TS",
                                ward_number=str(props.get("Ward_No", ward_id)),
                                ward_name=name,
                                parcels_count=0,
                                min_lon=bbox[0], min_lat=bbox[1],
                                max_lon=bbox[2], max_lat=bbox[3],
                                centroid_lat=c_lat, centroid_lon=c_lon,
                                geometry_json=json.dumps(norm_geom_dict),
                                properties_json=json.dumps(props)
                            ))
                        session.add_all(hyd_wards)
                        session.commit()
                        print(f"[Database] Loaded {len(hyd_wards)} Hyderabad GHMC wards into DB.")

                    # B. Seed Flagship Wards for Bengaluru, Mumbai, Delhi
                    other_wards = [
                        # Bengaluru (BBMP)
                        {"id": "KA-BLR-W198", "city": "KA-BLR", "state": "KA", "name": "Ward 198 (Electronic City)", "lat": 12.8452, "lon": 77.6602, "delta": 0.015},
                        {"id": "KA-BLR-W151", "city": "KA-BLR", "state": "KA", "name": "Ward 151 (Koramangala)", "lat": 12.9352, "lon": 77.6245, "delta": 0.012},
                        {"id": "KA-BLR-W84",  "city": "KA-BLR", "state": "KA", "name": "Ward 84 (Whitefield)", "lat": 12.9698, "lon": 77.7499, "delta": 0.018},
                        {"id": "KA-BLR-W80",  "city": "KA-BLR", "state": "KA", "name": "Ward 80 (Indiranagar)", "lat": 12.9784, "lon": 77.6408, "delta": 0.011},
                        {"id": "KA-BLR-W168", "city": "KA-BLR", "state": "KA", "name": "Ward 168 (Jayanagar)", "lat": 12.9308, "lon": 77.5838, "delta": 0.012},
                        {"id": "KA-BLR-W174", "city": "KA-BLR", "state": "KA", "name": "Ward 174 (HSR Layout)", "lat": 12.9121, "lon": 77.6446, "delta": 0.014},
                        # Mumbai (BMC)
                        {"id": "MH-MUM-WA",   "city": "MH-MUM", "state": "MH", "name": "Ward A (Colaba & Nariman Point)", "lat": 18.9220, "lon": 72.8258, "delta": 0.012},
                        {"id": "MH-MUM-WHW",  "city": "MH-MUM", "state": "MH", "name": "Ward H-West (Bandra West)", "lat": 19.0596, "lon": 72.8295, "delta": 0.013},
                        {"id": "MH-MUM-WKE",  "city": "MH-MUM", "state": "MH", "name": "Ward K-East (Andheri East)", "lat": 19.1136, "lon": 72.8697, "delta": 0.016},
                        {"id": "MH-MUM-WHE",  "city": "MH-MUM", "state": "MH", "name": "Ward H-East (Bandra Kurla Complex - BKC)", "lat": 19.0657, "lon": 72.8643, "delta": 0.012},
                        # Delhi (MCD)
                        {"id": "DL-DEL-W01",  "city": "DL-DEL", "state": "DL", "name": "Ward 01 (Connaught Place)", "lat": 28.6315, "lon": 77.2167, "delta": 0.012},
                        {"id": "DL-DEL-W02",  "city": "DL-DEL", "state": "DL", "name": "Ward 02 (Chanakyapuri)", "lat": 28.5983, "lon": 77.1895, "delta": 0.015},
                        {"id": "DL-DEL-W03",  "city": "DL-DEL", "state": "DL", "name": "Ward 03 (Rohini)", "lat": 28.7166, "lon": 77.1186, "delta": 0.018},
                        {"id": "DL-DEL-W04",  "city": "DL-DEL", "state": "DL", "name": "Ward 04 (Dwarka)", "lat": 28.5921, "lon": 77.0460, "delta": 0.018},
                        {"id": "DL-DEL-W05",  "city": "DL-DEL", "state": "DL", "name": "Ward 05 (Saket)", "lat": 28.5245, "lon": 77.2066, "delta": 0.014}
                    ]
                    other_ward_objs = []
                    for ow in other_wards:
                        d = ow["delta"]
                        poly = box(ow["lon"] - d, ow["lat"] - d, ow["lon"] + d, ow["lat"] + d)
                        other_ward_objs.append(WardTable(
                            ward_id=ow["id"],
                            city_id=ow["city"],
                            state_code=ow["state"],
                            ward_number=ow["id"].split("-")[-1],
                            ward_name=ow["name"],
                            parcels_count=0,
                            min_lon=ow["lon"] - d, min_lat=ow["lat"] - d,
                            max_lon=ow["lon"] + d, max_lat=ow["lat"] + d,
                            centroid_lat=ow["lat"], centroid_lon=ow["lon"],
                            geometry_json=json.dumps(shapely_to_geojson(poly)),
                            properties_json=json.dumps({"source": "Flagship ULB Boundary"})
                        ))
                    session.add_all(other_ward_objs)
                    session.commit()
                    print(f"[Database] Seeded {len(other_ward_objs)} Flagship Wards across Bengaluru, Mumbai, and Delhi.")

                # 4. Seed initial default parcels for Hyderabad Ward 1 if empty
                if session.query(ParcelTable).count() == 0:
                    session.close()
                    for wid in ["1", "0"]:
                        try:
                            self.generate_ward_parcels(ward_id=wid, target_parcels=14, source="synthetic")
                            print(f"[Seed] Generated default 3D parcels for Hyderabad Ward ID {wid}")
                        except Exception as e:
                            print(f"[Seed] Note on seeding ward {wid}: {e}")
            except Exception as e:
                session.rollback()
                print(f"[Database] Seeding error: {e}")
            finally:
                session.close()

    # ---------------------------------------------------------
    # State & City Queries
    # ---------------------------------------------------------

    def get_all_states(self) -> List[Dict[str, Any]]:
        """Return all states with city and ward counts."""
        session = self.get_session()
        try:
            states = session.query(StateTable).order_by(StateTable.state_name).all()
            res = []
            for s in states:
                city_count = session.query(CityTable).filter_by(state_code=s.state_code).count()
                ward_count = session.query(WardTable).filter_by(state_code=s.state_code).count()
                res.append({
                    "state_code": s.state_code,
                    "state_name": s.state_name,
                    "country_code": s.country_code,
                    "center": [s.center_lat, s.center_lon],
                    "bbox": [s.min_lon, s.min_lat, s.max_lon, s.max_lat],
                    "cities_count": city_count,
                    "wards_count": ward_count
                })
            return res
        finally:
            session.close()

    def get_cities_by_state(self, state_code: str) -> List[Dict[str, Any]]:
        """Return all cities for a specific state."""
        session = self.get_session()
        try:
            cities = session.query(CityTable).filter_by(state_code=state_code.upper()).order_by(CityTable.city_name).all()
            res = []
            for c in cities:
                ward_count = session.query(WardTable).filter_by(city_id=c.city_id).count()
                res.append({
                    "city_id": c.city_id,
                    "state_code": c.state_code,
                    "city_name": c.city_name,
                    "ulb_type": c.ulb_type,
                    "center": [c.center_lat, c.center_lon],
                    "bbox": [c.min_lon, c.min_lat, c.max_lon, c.max_lat],
                    "wards_count": ward_count
                })
            return res
        finally:
            session.close()

    def get_wards_by_city(self, city_id: str) -> List[Dict[str, Any]]:
        """Return all wards belonging to a city."""
        session = self.get_session()
        try:
            wards = session.query(WardTable).filter_by(city_id=city_id).order_by(WardTable.ward_name).all()
            res = []
            for w in wards:
                res.append({
                    "id": w.ward_id,
                    "city_id": w.city_id,
                    "state_code": w.state_code,
                    "name": w.ward_name,
                    "parcels_count": w.parcels_count,
                    "bbox": [w.min_lon, w.min_lat, w.max_lon, w.max_lat],
                    "centroid": [w.centroid_lat, w.centroid_lon]
                })
            return res
        finally:
            session.close()

    # ---------------------------------------------------------
    # Ward & Parcel Queries (DB-First Caching)
    # ---------------------------------------------------------

    def get_all_wards(self, city_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return list of wards with parcel counts."""
        max_retries = 2
        for attempt in range(max_retries):
            session = self.get_session()
            try:
                query = session.query(WardTable)
                if city_id:
                    query = query.filter_by(city_id=city_id)
                wards = query.order_by(WardTable.parcels_count.desc(), WardTable.ward_name).all()
                
                res = []
                for w in wards:
                    ward_dict = {
                        "id": w.ward_id,
                        "city_id": w.city_id,
                        "state_code": w.state_code,
                        "name": w.ward_name,
                        "parcels_count": w.parcels_count,
                        "bbox": [w.min_lon, w.min_lat, w.max_lon, w.max_lat],
                        "centroid": [w.centroid_lat, w.centroid_lon],
                        "geometry": json.loads(w.geometry_json) if w.geometry_json else {},
                        "properties": json.loads(w.properties_json) if w.properties_json else {}
                    }
                    res.append(ward_dict)
                    if hasattr(self, "_wards_cache"):
                        self._wards_cache[str(w.ward_id)] = ward_dict
                return res
            except Exception as e:
                if not self.is_sqlite and attempt < max_retries - 1:
                    print(f"[Database] Retrying get_all_wards after connection error: {e}")
                    try:
                        self.engine.dispose()
                    except Exception:
                        pass
                    continue
                raise e
            finally:
                session.close()
        return []

    def get_ward(self, ward_id: str) -> Optional[Dict[str, Any]]:
        """Get single ward details and boundary geometry."""
        ward_key = str(ward_id)
        if hasattr(self, "_wards_cache") and ward_key in self._wards_cache:
            return self._wards_cache[ward_key]

        max_retries = 2
        for attempt in range(max_retries):
            session = self.get_session()
            try:
                w = session.query(WardTable).filter_by(ward_id=ward_key).first()
                if not w:
                    return None
                ward_dict = {
                    "id": w.ward_id,
                    "city_id": w.city_id,
                    "state_code": w.state_code,
                    "name": w.ward_name,
                    "parcels_count": w.parcels_count,
                    "bbox": [w.min_lon, w.min_lat, w.max_lon, w.max_lat],
                    "centroid": [w.centroid_lat, w.centroid_lon],
                    "geometry": json.loads(w.geometry_json) if w.geometry_json else {},
                    "properties": json.loads(w.properties_json) if w.properties_json else {}
                }
                if hasattr(self, "_wards_cache"):
                    self._wards_cache[ward_key] = ward_dict
                return ward_dict
            except Exception as e:
                if not self.is_sqlite and attempt < max_retries - 1:
                    print(f"[Database] Retrying get_ward after connection error: {e}")
                    try:
                        self.engine.dispose()
                    except Exception:
                        pass
                    continue
                raise e
            finally:
                session.close()
        return None

    def get_parcels(
        self,
        ward_id: Optional[str] = None,
        land_use: Optional[str] = None,
        search: Optional[str] = None,
        bbox: Optional[List[float]] = None,
        source: str = "osm",
        limit: Optional[int] = 60
    ) -> List[Dict[str, Any]]:
        """
        DB-First Parcel Retrieval:
        1. Checks in-memory cache for ultra-fast (sub-millisecond) response.
        2. Checks database table `parcels`.
        3. If parcels exist in DB -> returns them immediately and caches in memory.
        4. If NO parcels exist in DB for this ward -> generates them via OSM / synthetic cadastre,
           persists them into PostgreSQL (capped at 50 parcels), and caches them for all future requests.
        """
        # 1. Fast in-memory cache lookup for ward queries
        if ward_id and not land_use and not search and not bbox:
            ward_str = str(ward_id)
            if ward_str in self._parcels_cache:
                cached = self._parcels_cache[ward_str]
                return cached[:limit] if limit and limit > 0 else cached

        max_retries = 2
        for attempt in range(max_retries):
            session = self.get_session()
            try:
                # Build Query
                query = session.query(ParcelTable)
                if ward_id:
                    query = query.filter_by(ward_id=str(ward_id))

                if isinstance(land_use, str) and land_use.lower() != "all":
                    query = query.filter(func.lower(ParcelTable.land_use) == land_use.lower())

                if bbox and len(bbox) == 4:
                    min_x, min_y, max_x, max_y = bbox
                    query = query.filter(
                        ParcelTable.centroid_lon >= min_x,
                        ParcelTable.centroid_lon <= max_x,
                        ParcelTable.centroid_lat >= min_y,
                        ParcelTable.centroid_lat <= max_y
                    )

                if isinstance(search, str) and search.strip():
                    term = f"%{search.strip().upper()}%"
                    query = query.filter(
                        (func.upper(ParcelTable.ulpin).like(term)) |
                        (func.upper(ParcelTable.parcel_id).like(term)) |
                        (func.upper(ParcelTable.owner_name).like(term)) |
                        (func.upper(ParcelTable.survey_number).like(term))
                    )

                if limit and limit > 0:
                    query = query.limit(limit)

                parcels_db = query.all()

                # 2. If ward has no parcels in DB yet, generate on-demand, persist up to 50 to DB, and cache all
                if not parcels_db and ward_id and not land_use and not search and not bbox:
                    session.close()
                    new_parcels = self.generate_ward_parcels(ward_id=str(ward_id), target_parcels=None, source=source, max_db_parcels=50)
                    return new_parcels[:limit] if limit and limit > 0 else new_parcels

                results = []
                for p in parcels_db:
                    geom = json.loads(p.geometry_json) if p.geometry_json else {}
                    ext = json.loads(p.extrusion_json) if p.extrusion_json else {}
                    p_dict = {
                        "parcel_id": p.parcel_id,
                        "ward_id": p.ward_id,
                        "ulpin": p.ulpin,
                        "survey_number": p.survey_number,
                        "land_use": p.land_use,
                        "owner_name": p.owner_name,
                        "area_sqm": p.area_sqm,
                        "centroid": {"lat": p.centroid_lat, "lon": p.centroid_lon},
                        "buildings_count": p.buildings_count,
                        "floors_count": p.floors_count,
                        "data_source": p.data_source,
                        "is_persisted_to_db": True,
                        "geometry": geom,
                        "extrusion": ext
                    }
                    results.append(p_dict)
                    self._parcels_lookup[p.parcel_id] = p_dict
                    self._parcels_lookup[p.ulpin] = p_dict

                # Populate fast in-memory cache
                if ward_id and not land_use and not search and not bbox:
                    self._parcels_cache[str(ward_id)] = results

                return results
            except Exception as e:
                if not self.is_sqlite and attempt < max_retries - 1:
                    print(f"[Database] Retrying get_parcels after connection error: {e}")
                    try:
                        self.engine.dispose()
                    except Exception:
                        pass
                    continue
                raise e
            finally:
                session.close()
        return []

    def generate_ward_parcels(
        self,
        ward_id: str,
        target_parcels: Optional[int] = None,
        source: str = "osm",
        max_db_parcels: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Generate cadastral parcels, 3D buildings, and 3D ULPINs for a ward.
        Saves and commits up to 50 generated parcels and 3D units directly into PostgreSQL.
        Uses zero-idle DB pattern: external OSM queries are run entirely outside of any DB session.
        """
        with self._lock:
            # 1. Fetch ward geometry in a quick read session (closed immediately)
            ward_data = self.get_ward(str(ward_id))
            if not ward_data:
                raise ValueError(f"Ward with ID {ward_id} not found")

            city_id = ward_data.get("city_id", "HYD")
            state_code = ward_data.get("state_code", "TG")
            ward_geom_dict = ward_data.get("geometry", {})

            sh_geom = geojson_to_shapely(ward_geom_dict)
            if isinstance(sh_geom, MultiPolygon):
                sh_geom = max(sh_geom.geoms, key=lambda g: g.area)

            # 2. Generate parcels outside DB session (NO connection held open during network I/O)
            if source.lower() == "osm":
                all_parcels = generate_parcels_from_osm(
                    ward_polygon_wgs84=sh_geom,
                    ward_id=str(ward_id),
                    max_parcels=target_parcels
                )
            else:
                all_parcels = partition_ward_into_parcels(
                    ward_polygon_wgs84=sh_geom,
                    ward_id=str(ward_id),
                    target_parcels=target_parcels or 1200
                )

            # 3. Select any 50 parcels to persist into PostgreSQL (preserving DB quota)
            db_parcels = [p for p in all_parcels if p.get("is_persisted_to_db")]
            if not db_parcels:
                db_parcels = all_parcels[:max_db_parcels]
            elif max_db_parcels and len(db_parcels) > max_db_parcels:
                db_parcels = db_parcels[:max_db_parcels]

            db_pids = {p["parcel_id"] for p in db_parcels}
            for p in all_parcels:
                p["is_persisted_to_db"] = p["parcel_id"] in db_pids

            # 4. Prepare ORM records
            parcel_records = []
            building_records = []
            floor_records = []

            for p in db_parcels:
                pid = p["parcel_id"]
                ulpin = p["ulpin"]
                c = p.get("centroid", {})
                c_lat = c.get("lat", 0.0)
                c_lon = c.get("lon", 0.0)

                p_sh = geojson_to_shapely(p["geometry"])
                p_bbox = list(p_sh.bounds)

                ext = p.get("extrusion") or {}
                buildings = ext.get("buildings", [])

                parcel_records.append(ParcelTable(
                    parcel_id=pid,
                    ulpin=ulpin,
                    ward_id=str(ward_id),
                    city_id=city_id,
                    state_code=state_code,
                    survey_number=p.get("survey_number", "Sy. 1"),
                    land_use=p.get("land_use", "Residential"),
                    owner_name=p.get("owner_name", "Government"),
                    area_sqm=p.get("area_sqm", 0.0),
                    centroid_lat=c_lat,
                    centroid_lon=c_lon,
                    min_lon=p_bbox[0], min_lat=p_bbox[1],
                    max_lon=p_bbox[2], max_lat=p_bbox[3],
                    buildings_count=len(buildings) if buildings else 1,
                    floors_count=buildings[0].get("floors_count", 1) if buildings else 1,
                    data_source=p.get("data_source", source.upper()),
                    geometry_json=json.dumps(p["geometry"]),
                    extrusion_json=json.dumps(ext)
                ))

                for b in buildings:
                    bid = b.get("building_id", f"{pid}-B1")
                    building_records.append(BuildingTable(
                        building_id=bid,
                        parcel_id=pid,
                        building_name=b.get("building_name", "Structure"),
                        floors_count=b.get("floors_count", 1),
                        height_m=b.get("height_m", 3.2),
                        base_elevation_m=b.get("base_elevation_m", 510.0),
                        roof_elevation_m=b.get("roof_elevation_m", 513.2),
                        footprint_area_sqm=b.get("footprint_area_sqm", 0.0),
                        built_up_area_sqm=b.get("built_up_area_sqm", 0.0)
                    ))

                    for fl in b.get("floors", []):
                        floor_records.append(FloorUnitTable(
                            ulpin_3d=fl.get("ulpin_3d", f"{ulpin}-F{fl.get('floor_index', 0):02d}"),
                            building_id=bid,
                            parcel_id=pid,
                            floor_index=fl.get("floor_index", 0),
                            floor_label=fl.get("floor_label", "Floor 0"),
                            z_min=fl.get("z_min", 0.0),
                            z_max=fl.get("z_max", 3.2),
                            height=fl.get("height", 3.2),
                            area_sqm=fl.get("area_sqm", 0.0),
                            unit_type=fl.get("unit_type", "Apartment Unit"),
                            unit_id=fl.get("unit_id", f"{pid}-U1")
                        ))

            # 5. Fast, isolated DB write (~50ms) with automatic retry on dead connection
            max_retries = 2
            for attempt in range(max_retries):
                session = self.get_session()
                try:
                    existing_pids = [r[0] for r in session.query(ParcelTable.parcel_id).filter_by(ward_id=str(ward_id)).all()]
                    if existing_pids:
                        session.query(FloorUnitTable).filter(FloorUnitTable.parcel_id.in_(existing_pids)).delete(synchronize_session=False)
                        session.query(BuildingTable).filter(BuildingTable.parcel_id.in_(existing_pids)).delete(synchronize_session=False)
                        session.query(ParcelTable).filter_by(ward_id=str(ward_id)).delete(synchronize_session=False)

                    session.add_all(parcel_records)
                    session.add_all(building_records)
                    session.add_all(floor_records)

                    # Update ward count
                    ward_record = session.query(WardTable).filter_by(ward_id=str(ward_id)).first()
                    if ward_record:
                        ward_record.parcels_count = len(db_parcels)

                    session.commit()
                    break
                except Exception as e:
                    session.rollback()
                    if not self.is_sqlite and attempt < max_retries - 1:
                        print(f"[Database] Retrying DB persistence after error: {e}")
                        try:
                            self.engine.dispose()
                        except Exception:
                            pass
                        continue
                    print(f"[Database Warning] Could not persist parcels to DB: {e}")
                finally:
                    session.close()

            # 6. Cache the 50 DB parcels for ward re-visits, while keeping all in parcels_lookup for 3D inspection
            self._parcels_cache[str(ward_id)] = db_parcels
            if hasattr(self, "_wards_cache") and str(ward_id) in self._wards_cache:
                self._wards_cache[str(ward_id)]["parcels_count"] = len(db_parcels)

            for p in all_parcels:
                self._parcels_lookup[p["parcel_id"]] = p
                self._parcels_lookup[p["ulpin"]] = p

            print(f"[Database] Generated all {len(all_parcels)} parcels for Ward {ward_id}. Persisted {len(db_parcels)} parcels and {len(floor_records)} 3D floor units into PostgreSQL.")
            return all_parcels

    def get_parcel(self, parcel_id: str) -> Optional[Dict[str, Any]]:
        """Get parcel by ID or 14-character ULPIN from memory cache or DB."""
        pid_clean = str(parcel_id).strip()
        pid_upper = pid_clean.upper()

        # 1. Fast in-memory lookup first (0ms latency)
        p_mem = self._parcels_lookup.get(pid_clean) or self._parcels_lookup.get(pid_upper)
        if p_mem:
            if not p_mem.get("extrusion") and "buildings_wgs84" in p_mem:
                try:
                    p_geom = geojson_to_shapely(p_mem["geometry"])
                    p_mem["extrusion"] = extrude_parcel_and_buildings(
                        parcel_wgs84=p_geom,
                        buildings_wgs84=p_mem["buildings_wgs84"],
                        parcel_ulpin=p_mem["ulpin"],
                        parcel_id=p_mem["parcel_id"],
                        land_use=p_mem.get("land_use", "Residential"),
                        owner_name=p_mem.get("owner_name", "Unknown")
                    )
                except Exception as e:
                    print(f"[Database Warning] On-demand extrusion error: {e}")
            return p_mem

        # 2. Database lookup with retry
        max_retries = 2
        for attempt in range(max_retries):
            session = self.get_session()
            try:
                p = session.query(ParcelTable).filter(
                    (ParcelTable.parcel_id == pid_clean) | (ParcelTable.ulpin == pid_upper)
                ).first()

                if p:
                    p_dict = {
                        "parcel_id": p.parcel_id,
                        "ward_id": p.ward_id,
                        "ulpin": p.ulpin,
                        "survey_number": p.survey_number,
                        "land_use": p.land_use,
                        "owner_name": p.owner_name,
                        "area_sqm": p.area_sqm,
                        "centroid": {"lat": p.centroid_lat, "lon": p.centroid_lon},
                        "buildings_count": p.buildings_count,
                        "floors_count": p.floors_count,
                        "data_source": p.data_source,
                        "is_persisted_to_db": True,
                        "geometry": json.loads(p.geometry_json) if p.geometry_json else {},
                        "extrusion": json.loads(p.extrusion_json) if p.extrusion_json else {}
                    }
                    self._parcels_lookup[p.parcel_id] = p_dict
                    self._parcels_lookup[p.ulpin] = p_dict
                    return p_dict
                return None
            except Exception as e:
                if not self.is_sqlite and attempt < max_retries - 1:
                    print(f"[Database] Retrying get_parcel after connection error: {e}")
                    try:
                        self.engine.dispose()
                    except Exception:
                        pass
                    continue
                print(f"[Database Error] get_parcel({parcel_id}) error: {e}")
                return None
            finally:
                session.close()

        return None

    def get_parcel_lidar(self, parcel_id: str) -> List[Dict[str, Any]]:
        """Get or generate synthetic LiDAR points for a parcel and cache in memory and DB."""
        if parcel_id in self._lidar_cache:
            return self._lidar_cache[parcel_id]

        session = self.get_session()
        try:
            cached = session.query(LiDARCacheTable).filter_by(parcel_id=parcel_id).first()
            if cached:
                pts = json.loads(cached.points_json)
                self._lidar_cache[parcel_id] = pts
                return pts

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

            # Cache in DB
            new_cache = LiDARCacheTable(
                parcel_id=parcel_id,
                points_count=len(points),
                points_json=json.dumps(points)
            )
            session.merge(new_cache)
            session.commit()
            self._lidar_cache[parcel_id] = points
            return points
        finally:
            session.close()

    def get_stats(self) -> Dict[str, Any]:
        """Aggregate summary statistics across all states, cities, and parcels."""
        session = self.get_session()
        try:
            total_states = session.query(StateTable).count()
            total_cities = session.query(CityTable).count()
            total_wards = session.query(WardTable).count()
            active_wards = session.query(func.count(func.distinct(ParcelTable.ward_id))).scalar() or 0
            total_parcels = session.query(ParcelTable).count()
            total_buildings = session.query(BuildingTable).count()
            total_3d_units = session.query(FloorUnitTable).count()

            total_land_area = session.query(func.sum(ParcelTable.area_sqm)).scalar() or 0.0
            total_built_up_area = session.query(func.sum(BuildingTable.built_up_area_sqm)).scalar() or 0.0

            land_use_counts = {"Residential": 0, "Commercial": 0, "Mixed Use": 0, "Institutional": 0}
            lu_results = session.query(ParcelTable.land_use, func.count(ParcelTable.parcel_id)).group_by(ParcelTable.land_use).all()
            for lu, count in lu_results:
                if lu in land_use_counts:
                    land_use_counts[lu] = count
                else:
                    land_use_counts[lu] = count

            return {
                "total_states": total_states,
                "total_cities": total_cities,
                "total_wards": total_wards,
                "active_wards_with_parcels": active_wards,
                "total_parcels": total_parcels,
                "total_buildings": total_buildings,
                "total_3d_units": total_3d_units,
                "total_land_area_sqm": round(float(total_land_area), 2),
                "total_built_up_area_sqm": round(float(total_built_up_area), 2),
                "land_use_breakdown": land_use_counts
            }
        finally:
            session.close()


# Global database singleton
db_instance = SpatialDatabase()