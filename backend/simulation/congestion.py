# backend/simulation/congestion.py
"""
Road congestion manager.
Tracks vehicle/person density per road segment and applies speed penalties.
"""

import math

class CongestionManager:
    def __init__(self):
        # key = (nodeA, nodeB), value = dict(capacity, density)
        self.segment_data = {}

        # Default capacity (agents per segment)
        self.default_capacity = 8

        # How strong congestion affects speed
        self.congestion_strength = 0.8  # adjust later

    def register_segment(self, a, b, capacity=None):
        """
        Register a segment between node a and node b.
        """
        key = tuple(sorted([a, b]))
        if key not in self.segment_data:
            self.segment_data[key] = {
                "capacity": capacity or self.default_capacity,
                "density": 0
            }

    def enter_segment(self, a, b):
        key = tuple(sorted([a, b]))
        if key not in self.segment_data:
            return
        self.segment_data[key]["density"] += 1

    def leave_segment(self, a, b):
        key = tuple(sorted([a, b]))
        if key not in self.segment_data:
            return
        self.segment_data[key]["density"] = max(
            0, self.segment_data[key]["density"] - 1
        )

    def get_speed_multiplier(self, a, b):
        """
        Returns a multiplier applied to agent speed.
        < 1 = slowdown caused by congestion
        """
        key = tuple(sorted([a, b]))
        if key not in self.segment_data:
            return 1.0

        cap = self.segment_data[key]["capacity"]
        dens = self.segment_data[key]["density"]

        if dens <= cap:
            return 1.0  # no slowdown

        # congestion factor increases as density exceeds capacity
        overflow = dens - cap

        return 1.0 / (1.0 + self.congestion_strength * overflow)

    def get_density(self, a, b):
        key = tuple(sorted([a, b]))
        if key not in self.segment_data:
            return 0
        return self.segment_data[key]["density"]

    def debug_summary(self):
        return {
            str(key): val for key, val in self.segment_data.items()
        }
