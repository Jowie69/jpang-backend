# backend/simulation/agents.py
from mesa import Agent
import math

def lonlat_to_latlon(coord):
    """Convert (lon, lat) -> (lat, lon) for mapping libraries like Leaflet."""
    lon, lat = coord
    return (lat, lon)

def interpolate(a, b, t):
    """Linear interpolation between 2 points a=(x,y), b=(x,y) for t in [0,1]."""
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

class EvacCenterAgent(Agent):
    def __init__(self, unique_id, model, loc, capacity=99999, name=None):
        """
        loc: (lon, lat)
        capacity: maximum number of agents it can accept
        """
        super().__init__(unique_id, model)
        self.loc = loc
        self.capacity = capacity
        self.occupants = 0
        self.name = name or f"evac_{unique_id}"

    def can_accept(self):
        return self.occupants < self.capacity

    def add_occupant(self):
        if self.can_accept():
            self.occupants += 1
            return True
        return False

    def step(self):
        # Evac center is passive for now
        return


class PersonAgent(Agent):
    def __init__(self, unique_id, model, home_loc, speed=1.4):
        """
        home_loc: (lon, lat) tuple - source location
        speed: meters per second (approx). Default 1.4 m/s walking.
        """
        super().__init__(unique_id, model)
        self.home = home_loc              # (lon, lat)
        self.pos = home_loc               # current (lon, lat)
        self.speed = speed                # m/s (base)
        self.state = "idle"               # 'idle', 'evacuating', 'safe', 'stuck', 'overtaken'
        self.evac_center_id = None        # unique_id of EvacCenterAgent
        self.route = []                   # list of (lon, lat) nodes from routing (path)
        self._route_idx = 0               # index of current target node in route
        self._segment_progress = 0.0      # fraction [0..1] along current segment
        self.reached = False

    # -------------------------
    # Route assignment + congestion entry
    # -------------------------
    def assign_route(self, route_nodes, evac_center_id):
        """
        route_nodes: list of (lon, lat) nodes returned by NetworkX routing function
        evac_center_id: id of the EvacCenterAgent target
        """
        # cleanup previous segment occupancy if any
        try:
            # if agent was occupying a segment, leave it
            prev_start, prev_end = self._current_segment()
            if prev_start is not None:
                self.model.congestion.leave_segment(prev_start, prev_end)
        except Exception:
            pass

        if not route_nodes or len(route_nodes) < 2:
            # nothing to do
            self.route = []
            self.evac_center_id = None
            self.state = "stuck"
            return

        self.route = route_nodes
        self._route_idx = 0
        self._segment_progress = 0.0
        self.evac_center_id = evac_center_id
        self.state = "evacuating"

        # register entry into first segment (if exists)
        try:
            a = self.route[0]
            b = self.route[1]
            self.model.congestion.enter_segment(a, b)
        except Exception:
            pass

    def _current_segment(self):
        """Return (start_coord, end_coord) of segment agent is traversing (lon, lat)."""
        if not self.route or self._route_idx >= len(self.route) - 1:
            return None, None
        return self.route[self._route_idx], self.route[self._route_idx + 1]

    def _segment_length_m(self, a, b):
        """
        Approximate distance between a=(lon,lat) and b=(lon,lat) in meters.
        Haversine formula for reasonable accuracy at municipal scale.
        """
        lon1, lat1 = a
        lon2, lat2 = b
        R = 6371000  # Earth radius in meters
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)

        hav = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
        c = 2 * math.atan2(math.sqrt(hav), math.sqrt(1-hav))
        return R * c

    # -------------------------
    # Movement step
    # -------------------------
    def step(self):
        """
        Move the agent along its route according to speed and time step.
        Model step treated as 1 second * step_time_seconds (model controls multiplier).
        """
        if self.state != "evacuating":
            return

        # if already at the last node
        if self._route_idx >= len(self.route) - 1:
            # arrive at evac center
            self._arrive()
            return

        start_node, end_node = self._current_segment()
        if start_node is None:
            self.state = "stuck"
            return

        # compute segment length in meters
        seg_len = self._segment_length_m(start_node, end_node)
        if seg_len == 0:
            # jump to next segment
            # leaving current segment (if recorded)
            try:
                self.model.congestion.leave_segment(start_node, end_node)
            except Exception:
                pass

            self._route_idx += 1
            self._segment_progress = 0.0

            # entering next segment
            try:
                ns, ne = self._current_segment()
                if ns and ne:
                    self.model.congestion.enter_segment(ns, ne)
            except Exception:
                pass

            return

        # congestion speed multiplier
        try:
            speed_factor = self.model.congestion.get_speed_multiplier(start_node, end_node)
        except Exception:
            speed_factor = 1.0

        effective_speed = self.speed * speed_factor
        # model.step_time_seconds may differ from 1s; use it to scale movement per step
        step_seconds = getattr(self.model, "step_time_seconds", 1)
        # distance covered this step
        dist_this_step = effective_speed * step_seconds

        delta = dist_this_step / seg_len
        self._segment_progress += delta

        # if we reached or passed end of segment
        while self._segment_progress >= 1.0 and self._route_idx < len(self.route) - 1:
            # leaving current segment
            try:
                s_start, s_end = self._current_segment()
                if s_start and s_end:
                    self.model.congestion.leave_segment(s_start, s_end)
            except Exception:
                pass

            # move to the next node
            self._route_idx += 1
            self._segment_progress -= 1.0

            # entering next segment (if any)
            try:
                ns, ne = self._current_segment()
                if ns and ne:
                    self.model.congestion.enter_segment(ns, ne)
            except Exception:
                pass

        # update current position by interpolating between start and end nodes
        s = max(0.0, min(1.0, self._segment_progress))
        self.pos = interpolate(start_node, end_node, s)

        # check arrival (if within final node and last segment)
        if self._route_idx >= len(self.route) - 1:
            end_coord = self.route[-1]
            # small distance threshold in meters
            if self._segment_length_m(self.pos, end_coord) < 1.0:
                self._arrive()

        # -------------------------
        # Hazard check (agent-level)
        # -------------------------
        try:
            hazard = getattr(self.model, "hazard", None)
            if hazard is not None:
                arrival = hazard.get_time_to_inundation(self.pos)
                if arrival is not None and arrival <= getattr(self.model, "sim_time", 0.0):
                    # overtaken by tsunami
                    self._become_overtaken()
        except Exception:
            pass

    # -------------------------
    # Arrival / Overtaken handlers
    # -------------------------
    def _become_overtaken(self):
        """Mark agent as inundated / overtaken and clean up congestion footprint."""
        # remove from current segment density if present
        try:
            s_start, s_end = self._current_segment()
            if s_start and s_end:
                self.model.congestion.leave_segment(s_start, s_end)
        except Exception:
            pass

        self.state = "overtaken"
        self.reached = False
        # Optionally record time or logs (model's datacollector will pick up state)

    def _arrive(self):
        """Handle arrival to evacuation center."""
        # remove occupancy on current segment
        try:
            prev_start, prev_end = None, None
            # if route index > 0, previous segment exists
            if self._route_idx > 0:
                prev_start = self.route[self._route_idx - 1]
                prev_end = self.route[self._route_idx]
            if prev_start and prev_end:
                self.model.congestion.leave_segment(prev_start, prev_end)
        except Exception:
            pass

        # mark safe and notify evac center
        self.state = "safe"
        self.reached = True
        if self.evac_center_id is not None:
            agent = None
            try:
                # model stores evac agents in model.evac_agents dict
                agent = self.model.get_evac_agent_by_id(self.evac_center_id)
            except Exception:
                agent = None

            if agent is not None:
                agent.add_occupant()

    def get_latlon(self):
        """Return position as (lat, lon) for mapping (Leaflet)."""
        lon, lat = self.pos
        return (lat, lon)

    def info(self):
        return {
            "id": self.unique_id,
            "pos": self.pos,
            "latlon": self.get_latlon(),
            "state": self.state,
            "evac_center_id": self.evac_center_id
        }
