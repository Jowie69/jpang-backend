# backend/simulation/hazard.py
"""
Tsunami hazard helper.

Supports two modes:
  1) Precomputed inundation polygons (GeoJSON) with per-feature arrival times.
     The GeoJSON features should include a numeric property named one of:
         'arrival_time', 'time_to_inundation', 't_arrival' (units: seconds from t0)
     If arrival time is not present we'll treat polygon as 'inundated at t=0'.

  2) Dynamic propagation from source points with a given propagation_speed (m/s).
     Provide source_points as list of (lon, lat) or a GeoJSON/GeoDataFrame of points.

Main class: TsunamiHazard
  - update(current_time_seconds): sets the internal time
  - is_inundated(coord, time=None): returns True/False for (lon,lat)
  - get_time_to_inundation(coord): returns arrival time (seconds) or None
  - nearest_inundation_feature(coord): returns (geom, arrival_time) if available
"""

from typing import List, Tuple, Optional, Union
import geopandas as gpd
from shapely.geometry import shape, Point
from shapely.prepared import prep
from pyproj import Transformer
import math
import os

Coord = Tuple[float, float]  # (lon, lat)

# Transformer: WGS84 lon/lat -> WebMercator meters (EPSG:3857)
# Using EPSG:3857 avoids issues with distance in degrees. For more accuracy inside a region consider local UTM.
_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_TO_LONLAT = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def lonlat_to_meters(coord: Coord) -> Tuple[float, float]:
    lon, lat = coord
    x, y = _TO_M.transform(lon, lat)
    return x, y


def meters_distance(a_m: Tuple[float, float], b_m: Tuple[float, float]) -> float:
    dx = a_m[0] - b_m[0]
    dy = a_m[1] - b_m[1]
    return math.hypot(dx, dy)


class TsunamiHazard:
    def __init__(
        self,
        data_dir: str = "data",
        inundation_geojson: Optional[str] = None,
        source_points: Optional[List[Coord]] = None,
        propagation_speed: Optional[float] = None,
    ):
        """
        Parameters
        ----------
        data_dir: base path to find geojsons when only filename is provided
        inundation_geojson: path to geojson containing inundation polygons with arrival_time property (seconds)
        source_points: list of (lon, lat) points to propagate tsunami from (used if inundation_geojson is None)
        propagation_speed: tsunami propagation speed in meters/second for dynamic mode (required when source_points used)
        """
        self.data_dir = data_dir
        self.current_time = 0.0  # seconds since simulation t0

        # Mode flags
        self._has_precomputed = False
        self._has_dynamic = False

        # Precomputed mode storage
        # list of tuples (prepared_geom, arrival_time_seconds)
        self._polygons = []

        # Dynamic mode storage
        self._source_points_m = []  # source points in projected meters
        self._prop_speed = None

        if inundation_geojson:
            path = inundation_geojson
            if not os.path.isabs(path):
                path = os.path.join(self.data_dir, path)
            self._load_inundation_polygons(path)

        elif source_points and propagation_speed:
            self._load_dynamic_sources(source_points, propagation_speed)

        else:
            # No hazard data — hazard module exists but will say "no data"
            pass

    # -------------------------
    # Precomputed inundation
    # -------------------------
    def _load_inundation_polygons(self, path: str):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Inundation GeoJSON not found: {path}")

        gdf = gpd.read_file(path)
        # Ensure geometry column exists
        if "geometry" not in gdf:
            raise ValueError("Inundation file has no geometry column")

        self._polygons = []
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue

            # Normalize arrival time field names
            arrival_time = None
            for key in ("arrival_time", "time_to_inundation", "t_arrival", "t0"):
                if key in row and row[key] is not None:
                    try:
                        arrival_time = float(row[key])
                        break
                    except Exception:
                        pass

            if arrival_time is None:
                # If no attribute was present we take arrival_time = 0 (already inundated at t0)
                arrival_time = 0.0

            # store prepared geometry and its arrival time
            self._polygons.append((prep(geom), float(arrival_time)))

        self._has_precomputed = len(self._polygons) > 0

    # -------------------------
    # Dynamic propagation
    # -------------------------
    def _load_dynamic_sources(self, source_points: List[Coord], propagation_speed: float):
        self._prop_speed = float(propagation_speed)
        self._source_points_m = [lonlat_to_meters(pt) for pt in source_points]
        self._has_dynamic = len(self._source_points_m) > 0 and self._prop_speed > 0

    # -------------------------
    # Public interface
    # -------------------------
    def update(self, current_time_seconds: float):
        """Update internal current time (seconds since t0)."""
        self.current_time = float(current_time_seconds)

    def is_inundated(self, coord: Coord, time: Optional[float] = None) -> bool:
        """
        Return True if coordinate (lon,lat) is inundated at 'time' seconds.
        If time is None, uses the module's current_time.
        """
        t = self.current_time if time is None else float(time)
        arrival = self.get_time_to_inundation(coord)
        if arrival is None:
            return False
        return arrival <= t

    def get_time_to_inundation(self, coord: Coord) -> Optional[float]:
        """
        Return the arrival time (seconds since t0) for the given coordinate.
        Returns None if unknown (no hazard data).
        """
        # If precomputed polygons available, check membership first.
        if self._has_precomputed:
            pt = Point(coord)
            for prep_geom, arrival_time in self._polygons:
                try:
                    if prep_geom.contains(pt) or prep_geom.intersects(pt):
                        return float(arrival_time)
                except Exception:
                    # prepared geom may raise for invalid geometry; skip if problem
                    continue
            # Not inside any inundation polygon → not inundated (or arrival unknown)
            return None

        # Else if dynamic mode: compute minimal travel time from any source point
        if self._has_dynamic:
            coord_m = lonlat_to_meters(coord)
            min_dist = float("inf")
            for s in self._source_points_m:
                d = meters_distance(coord_m, s)
                if d < min_dist:
                    min_dist = d
            # arrival time = distance (m) / speed (m/s)
            if self._prop_speed and min_dist < float("inf"):
                return float(min_dist / self._prop_speed)
            return None

        # No hazard data
        return None

    def nearest_inundation_feature(self, coord: Coord) -> Optional[Tuple[object, float]]:
        """
        If precomputed polygons exist, return the first polygon (geom, arrival_time) that contains/intersects coord.
        Otherwise return None.
        """
        if not self._has_precomputed:
            return None
        pt = Point(coord)
        for prep_geom, arrival_time in self._polygons:
            try:
                if prep_geom.contains(pt) or prep_geom.intersects(pt):
                    # can't return the prepared geom itself as it isn't the original geom; return arrival_time only
                    return (prep_geom, arrival_time)
            except Exception:
                continue
        return None

    # Utilities for debugging / reporting
    def summary(self) -> dict:
        """Return a summary of what hazard data is loaded."""
        return {
            "has_precomputed": self._has_precomputed,
            "num_polygons": len(self._polygons),
            "has_dynamic": self._has_dynamic,
            "num_sources": len(self._source_points_m),
            "propagation_speed_m_s": self._prop_speed,
            "current_time_s": self.current_time,
        }
