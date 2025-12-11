import json

def load_geojson(path):
    """Load and return GeoJSON file content."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e)}
