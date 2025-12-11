from utils.graph_builder import build_road_graph
from simulation.routing import compute_shortest_route

G = build_road_graph("data/roads.geojson")

print("Nodes:", len(G.nodes()))
print("Edges:", len(G.edges()))

# Example coordinates in Jose Panganiban (replace with your actual data range)
start = (122.6910, 14.2850)
end = (122.6950, 14.2900)

route = compute_shortest_route(start, end)
print("Route:", route)
