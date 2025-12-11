# backend/simulation/model.py
import os
import geopandas as gpd
from mesa import Model
from mesa.time import RandomActivation
from mesa.datacollection import DataCollector

from .agents import PersonAgent, EvacCenterAgent
from .routing import compute_shortest_route, ROAD_GRAPH
from .congestion import CongestionManager
from .hazard import TsunamiHazard

import itertools
import math

class EvacuationModel(Model):
    """
    EvacuationModel: spawns PersonAgent from buildings.geojson, EvacCenterAgent from evac.geojson.
    Each PersonAgent is assigned to nearest evacuation center (by Euclidean quick lookup),
    a route is computed via compute_shortest_route(start=(lon,lat), end=(lon,lat)).
    """

    def __init__(self, data_dir="data", population_limit=None, step_time_seconds=1, reroute_threshold_s=30):
        super().__init__()
        self.data_dir = data_dir.rstrip("/")
        self.schedule = RandomActivation(self)
        self.step_time_seconds = step_time_seconds

        # containers and indexes
        self.person_id_gen = itertools.count(start=1000)
        self.evac_id_gen = itertools.count(start=1)
        self.evac_agents = {}   # id -> agent
        self.person_agents = {} # id -> agent

        # -----------------------------------------
        # LOAD DATA
        # -----------------------------------------
        self.buildings_gdf = gpd.read_file(f"{self.data_dir}/buildings.geojson")
        self.evac_gdf = gpd.read_file(f"{self.data_dir}/evac.geojson")

        # -----------------------------------------
        # SPAWN AGENTS
        # -----------------------------------------
        self._spawn_evac_centers()
        self._spawn_people(population_limit=population_limit)

        # -----------------------------------------
        # SIMULATION CLOCK
        # -----------------------------------------
        self.sim_time = 0.0  # seconds since simulation start

        # -----------------------------------------
        # TSUNAMI HAZARD MODEL (dynamic propagation)
        # -----------------------------------------
        # Coastal source points (lon, lat)
        coastal_sources = [
            (122.6955, 14.2895),  # central coastline
            (122.7030, 14.2935),  # northern coastline
            (122.6875, 14.2840),  # southern coastline
        ]
        propagation_speed = 15.0  # m/s (near-shore approximation)

        self.hazard = TsunamiHazard(
            source_points=coastal_sources,
            propagation_speed=propagation_speed
        )

        # -----------------------------------------
        # CONGESTION MANAGER
        # -----------------------------------------
        self.congestion = CongestionManager()
        # Register all road segments from routing.ROAD_GRAPH
        for u, v in ROAD_GRAPH.edges():
            self.congestion.register_segment(u, v)

        # -----------------------------------------
        # REROUTING THRESHOLD
        # -----------------------------------------
        # If hazard arrival - current_time <= reroute_threshold_s => force reroute
        self.reroute_threshold_s = reroute_threshold_s

        # -----------------------------------------
        # DATA COLLECTOR
        # -----------------------------------------
        self.datacollector = DataCollector(
            model_reporters={
                "num_agents": lambda m: len(m.person_agents),
                "num_safe": lambda m: sum(1 for p in m.person_agents.values() if p.reached),
                "num_overtaken": lambda m: sum(1 for p in m.person_agents.values() if p.state == "overtaken"),
            },
            agent_reporters={
                "state": lambda a: a.state if isinstance(a, PersonAgent) else None
            }
        )

    # -------------------------
    # Spawning methods
    # -------------------------
    def _spawn_evac_centers(self):
        for _, row in self.evac_gdf.iterrows():
            geom = row.geometry
            if geom is None:
                continue
            if geom.geom_type in ("Point", "MultiPoint"):
                lon, lat = geom.x, geom.y
            else:
                centroid = geom.representative_point()
                lon, lat = centroid.x, centroid.y

            uid = next(self.evac_id_gen)
            evac_agent = EvacCenterAgent(uid, self, (lon, lat), capacity=row.get("capacity", 9999), name=row.get("name"))
            self.schedule.add(evac_agent)
            self.evac_agents[uid] = evac_agent

    def _spawn_people(self, population_limit=None, default_per_building=1):
        spawned = 0
        for idx, row in self.buildings_gdf.iterrows():
            if population_limit and spawned >= population_limit:
                break

            geom = row.geometry
            if geom is None:
                continue

            if geom.geom_type in ("Point", "MultiPoint"):
                lon, lat = geom.x, geom.y
            else:
                centroid = geom.representative_point()
                lon, lat = centroid.x, centroid.y

            count = int(row.get("pop", default_per_building)) if "pop" in row else default_per_building

            for i in range(count):
                if population_limit and spawned >= population_limit:
                    break
                uid = next(self.person_id_gen)
                person = PersonAgent(uid, self, (lon, lat), speed=1.4)
                self.schedule.add(person)
                self.person_agents[uid] = person
                spawned += 1

        # after creating people, assign routes
        self._assign_routes_to_people()

    # -------------------------
    # Utility: nearest evac center (quick Euclidean)
    # -------------------------
    def _find_nearest_evac_center(self, point):
        """
        Naive nearest evac center by Euclidean degrees distance (quick).
        For better accuracy you may map to nearest graph node and compute real path distance.
        point: (lon, lat)
        """
        min_d = float("inf")
        best_id = None
        px, py = point
        for uid, evac in self.evac_agents.items():
            ex, ey = evac.loc
            d = (ex - px)**2 + (ey - py)**2
            if d < min_d:
                min_d = d
                best_id = uid
        return best_id

    # -------------------------
    # Route assignment and recompute
    # -------------------------
    def _assign_routes_to_people(self):
        # for each person, pick nearest evac center and compute a route via routing.compute_shortest_route
        for uid, person in list(self.person_agents.items()):
            nearest_evac_id = self._find_nearest_evac_center(person.home)
            evac_agent = self.evac_agents.get(nearest_evac_id)
            if evac_agent is None:
                continue

            start = person.home           # (lon, lat)
            end = evac_agent.loc          # (lon, lat)

            try:
                path = compute_shortest_route(start, end)  # returns list of (lon, lat)
            except Exception:
                path = []

            if path and len(path) >= 2:
                person.assign_route(path, evac_agent.unique_id)
            else:
                # if no path found, mark stuck
                person.state = "stuck"

    def recompute_route_for_person(self, person, prefer_nearest=True):
        """
        Recompute route for a person.
        prefer_nearest: if True and current evac center saturated, try nearest other center.
        """
        if person is None or not person.route:
            return False

        # If current evac center is full, try to find alternate center
        current_target = person.evac_center_id
        target_agent = self.get_evac_agent_by_id(current_target) if current_target else None

        # If full or cannot accept, find another center
        if target_agent is None or not target_agent.can_accept():
            # choose nearest center that can accept
            candidates = sorted(self.evac_agents.values(), key=lambda e: ((e.loc[0]-person.pos[0])**2 + (e.loc[1]-person.pos[1])**2))
            new_target = None
            for c in candidates:
                if c.can_accept():
                    new_target = c
                    break
            if new_target is None:
                # no available centers
                return False
            target_agent = new_target

        # compute new path to target_agent.loc
        try:
            new_path = compute_shortest_route(person.pos, target_agent.loc)
        except Exception:
            new_path = []

        if new_path and len(new_path) >= 2:
            person.assign_route(new_path, target_agent.unique_id)
            return True

        return False

    # -------------------------
    # Get positions for frontend
    # -------------------------
    def get_agent_positions(self):
        out = []
        for uid, p in self.person_agents.items():
            lat, lon = p.get_latlon()  # returns (lat, lon)
            out.append({
                "id": uid,
                "lat": lat,
                "lon": lon,
                "state": p.state
            })
        return out

    def get_evac_agent_by_id(self, id_):
        return self.evac_agents.get(id_)

    # -------------------------
    # Simulation step
    # -------------------------
    def step(self):
        """
        Advance the simulation by one time-step.
        We assume a model step ~ step_time_seconds (configurable).
        """
        # advance model clock
        self.sim_time += self.step_time_seconds

        # update hazard internal clock
        try:
            self.hazard.update(self.sim_time)
        except Exception:
            pass

        # advance agents
        self.schedule.step()

        # POST-STEP: check hazard overtakes, reroute if needed, enforce evac center capacity
        for uid, person in list(self.person_agents.items()):
            try:
                if person.state == "evacuating":
                    # 1) hazard overtaken check (centralized)
                    arrival = self.hazard.get_time_to_inundation(person.pos)
                    if arrival is not None and arrival <= self.sim_time:
                        # mark overtaken
                        person._become_overtaken()
                        continue

                    # 2) if hazard is imminent at agent location, trigger reroute
                    if arrival is not None:
                        time_left = arrival - self.sim_time
                        if time_left <= self.reroute_threshold_s:
                            # attempt to recompute route (this will also switch center if full)
                            self.recompute_route_for_person(person, prefer_nearest=True)

                    # 3) if agent's current evac center is full, reassign
                    if person.evac_center_id is not None:
                        evac_agent = self.get_evac_agent_by_id(person.evac_center_id)
                        if evac_agent is not None and not evac_agent.can_accept():
                            self.recompute_route_for_person(person, prefer_nearest=True)

                # if agent is safe or overtaken or stuck, ensure they are not occupying any segment
                if person.state in ("safe", "overtaken", "stuck"):
                    try:
                        s_start, s_end = person._current_segment()
                        if s_start and s_end:
                            self.congestion.leave_segment(s_start, s_end)
                    except Exception:
                        pass

            except Exception:
                # continue simulation even if one person errors
                continue

        # collect data
        self.datacollector.collect(self)
