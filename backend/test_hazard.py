# backend/test_hazard.py
from simulation.hazard import TsunamiHazard

# Dynamic test
sources = [(122.695, 14.29)]  # example lon,lat
hz = TsunamiHazard(source_points=sources, propagation_speed=10.0)
pt = (122.696, 14.29)
t = hz.get_time_to_inundation(pt)
print("arrival time (s):", t)
hz.update(0.0)
print("is inundated at t=0:", hz.is_inundated(pt, time=0.0))
hz.update(t + 1.0)
print("is inundated at t>arrival:", hz.is_inundated(pt))
