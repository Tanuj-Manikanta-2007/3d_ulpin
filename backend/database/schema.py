"""
schema.py
SQLAlchemy ORM models for 3D ULPIN Multi-State Cadastral Database.
Supports States, Cities/ULBs, Wards, Parcels, Buildings, Floor Units, and LiDAR caches.
Compatible with SQLite and PostgreSQL/PostGIS.
"""

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Text,
    ForeignKey,
    Index
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class StateTable(Base):
    __tablename__ = "states"

    state_code = Column(String(10), primary_key=True)  # e.g., 'TS', 'KA', 'MH', 'DL'
    state_name = Column(String(100), nullable=False)
    country_code = Column(String(10), default="IN")
    center_lat = Column(Float, nullable=False)
    center_lon = Column(Float, nullable=False)
    min_lon = Column(Float)
    min_lat = Column(Float)
    max_lon = Column(Float)
    max_lat = Column(Float)
    geometry_json = Column(Text, nullable=True)

    cities = relationship("CityTable", back_populates="state", cascade="all, delete-orphan")
    wards = relationship("WardTable", back_populates="state", cascade="all, delete-orphan")


class CityTable(Base):
    __tablename__ = "cities"

    city_id = Column(String(30), primary_key=True)  # e.g., 'TS-HYD', 'KA-BLR'
    state_code = Column(String(10), ForeignKey("states.state_code"), nullable=False, index=True)
    city_name = Column(String(100), nullable=False)
    ulb_type = Column(String(50), default="Municipal Corporation")  # GHMC, BBMP, BMC, MCD
    center_lat = Column(Float, nullable=False)
    center_lon = Column(Float, nullable=False)
    min_lon = Column(Float)
    min_lat = Column(Float)
    max_lon = Column(Float)
    max_lat = Column(Float)
    geometry_json = Column(Text, nullable=True)

    state = relationship("StateTable", back_populates="cities")
    wards = relationship("WardTable", back_populates="city", cascade="all, delete-orphan")


class WardTable(Base):
    __tablename__ = "wards"

    ward_id = Column(String(50), primary_key=True)  # e.g., 'TS-HYD-W1', '1' (backwards-compatible)
    city_id = Column(String(30), ForeignKey("cities.city_id"), nullable=False, index=True)
    state_code = Column(String(10), ForeignKey("states.state_code"), nullable=False, index=True)
    ward_number = Column(String(20))
    ward_name = Column(String(150), nullable=False)
    parcels_count = Column(Integer, default=0)
    min_lon = Column(Float)
    min_lat = Column(Float)
    max_lon = Column(Float)
    max_lat = Column(Float)
    centroid_lat = Column(Float)
    centroid_lon = Column(Float)
    geometry_json = Column(Text, nullable=False)
    properties_json = Column(Text, nullable=True)

    city = relationship("CityTable", back_populates="wards")
    state = relationship("StateTable", back_populates="wards")
    parcels = relationship("ParcelTable", back_populates="ward", cascade="all, delete-orphan")


class ParcelTable(Base):
    __tablename__ = "parcels"

    parcel_id = Column(String(50), primary_key=True)
    ulpin = Column(String(30), index=True, nullable=False)
    ward_id = Column(String(50), ForeignKey("wards.ward_id"), index=True, nullable=False)
    city_id = Column(String(30), nullable=True, index=True)
    state_code = Column(String(10), nullable=True, index=True)
    survey_number = Column(String(50))
    land_use = Column(String(50))
    owner_name = Column(String(150))
    area_sqm = Column(Float)
    centroid_lat = Column(Float)
    centroid_lon = Column(Float)
    min_lon = Column(Float, index=True)
    min_lat = Column(Float, index=True)
    max_lon = Column(Float, index=True)
    max_lat = Column(Float, index=True)
    buildings_count = Column(Integer, default=1)
    floors_count = Column(Integer, default=1)
    data_source = Column(String(50), default="Synthetic")
    geometry_json = Column(Text, nullable=False)
    extrusion_json = Column(Text, nullable=True)

    ward = relationship("WardTable", back_populates="parcels")
    buildings = relationship("BuildingTable", back_populates="parcel", cascade="all, delete-orphan")
    floor_units = relationship("FloorUnitTable", back_populates="parcel", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_parcel_bbox", "min_lon", "min_lat", "max_lon", "max_lat"),
    )


class BuildingTable(Base):
    __tablename__ = "buildings"

    building_id = Column(String(50), primary_key=True)
    parcel_id = Column(String(50), ForeignKey("parcels.parcel_id"), index=True, nullable=False)
    building_name = Column(String(100))
    floors_count = Column(Integer, default=1)
    height_m = Column(Float, default=3.2)
    base_elevation_m = Column(Float, default=510.0)
    roof_elevation_m = Column(Float, default=513.2)
    footprint_area_sqm = Column(Float, default=0.0)
    built_up_area_sqm = Column(Float, default=0.0)
    geometry_json = Column(Text, nullable=True)

    parcel = relationship("ParcelTable", back_populates="buildings")


class FloorUnitTable(Base):
    __tablename__ = "floor_units_3d"

    ulpin_3d = Column(String(40), primary_key=True)
    building_id = Column(String(50), index=True, nullable=True)
    parcel_id = Column(String(50), ForeignKey("parcels.parcel_id"), index=True, nullable=False)
    floor_index = Column(Integer, default=0)
    floor_label = Column(String(30))
    z_min = Column(Float, default=0.0)
    z_max = Column(Float, default=3.2)
    height = Column(Float, default=3.2)
    area_sqm = Column(Float, default=0.0)
    unit_type = Column(String(50), default="Apartment Unit")
    unit_id = Column(String(50))

    parcel = relationship("ParcelTable", back_populates="floor_units")


class LiDARCacheTable(Base):
    __tablename__ = "lidar_caches"

    parcel_id = Column(String(50), primary_key=True)
    points_count = Column(Integer, default=0)
    points_json = Column(Text, nullable=False)
