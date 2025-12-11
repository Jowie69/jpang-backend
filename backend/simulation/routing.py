import networkx as nx
from utils.graph_builder import build_road_graph

# Load graph only once at startup
ROAD_GRAPH = build_road_graph("data/roads.geojson")

def nearest_node(graph, point):
    """
    Find the nearest graph node to a given (lat, lon) coordinate.
    """
    min_dist = float("inf")
    nearest = None

    px, py = point

    for node in graph.nodes():
        nx, ny = node
        dist = (nx - px)**2 + (ny - py)**2   # squared distance is faster
        if dist < min_dist:
            min_dist = dist
            nearest = node

    return nearest

def compute_shortest_route(start, end):
    """
    Convert (lat, lon) → nearest graph nodes → run shortest path.
    Returns a list of coordinates along the route.
    """
    start_node = nearest_node(ROAD_GRAPH, start)
    end_node = nearest_node(ROAD_GRAPH, end)

    if start_node is None or end_node is None:
        return []

    path = nx.shortest_path(
        ROAD_GRAPH,
        source=start_node,
        target=end_node,
        weight="weight"
    )

    return path
