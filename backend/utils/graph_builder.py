import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString, MultiLineString
from math import sqrt

def distance(p1, p2):
    """Compute Euclidean distance in lat/lon degrees (approx only)."""
    return sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)

def build_road_graph(geojson_path):
    """
    Convert roads.geojson into a NetworkX graph.
    Nodes = road segment endpoints
    Edges = road segments with weights (length)
    """
    print("Loading roads...")
    roads = gpd.read_file(geojson_path)

    G = nx.Graph()

    for idx, row in roads.iterrows():
        geom = row.geometry

        if geom is None or geom.is_empty:
            continue

        # Normalize geometry → always get a list of LineStrings
        if isinstance(geom, LineString):
            lines = [geom]

        elif isinstance(geom, MultiLineString):
            lines = list(geom.geoms)   # correct way in Shapely 2.x

        else:
            # Skip other geometry types (Point, Polygon, GeometryCollection, etc.)
            continue

        # Expand each LineString into nodes/edges
        for line in lines:
            coords = list(line.coords)

            for i in range(len(coords) - 1):
                p1 = coords[i]
                p2 = coords[i + 1]

                # Add nodes
                G.add_node(p1)
                G.add_node(p2)

                # Weighted edges based on Euclidean distance
                dist = distance(p1, p2)
                G.add_edge(p1, p2, weight=dist)

    print(f"Graph created: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    return G
