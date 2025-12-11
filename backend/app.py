from flask import Flask, jsonify, request
from flask_cors import CORS
import os

from utils.geo_loader import load_geojson
from simulation.model import EvacuationModel

app = Flask(__name__)
CORS(app)

# Global model holder
SIM_MODEL = None


# ============================================================
# MAP LAYERS API
# ============================================================
@app.route("/api/v1/map/<layer>", methods=["GET"])
def get_map_layer(layer):
    valid_layers = {
        "boundary": "boundary.geojson",
        "roads": "roads.geojson",
        "buildings": "buildings.geojson",
        "evac": "evac.geojson",
    }

    if layer not in valid_layers:
        return jsonify({"error": "Invalid layer name"}), 400

    file_path = os.path.join("data", valid_layers[layer])
    data = load_geojson(file_path)
    return jsonify(data)


# ============================================================
# SIMULATION API
# ============================================================
@app.route("/api/v1/sim/start", methods=["GET", "POST"])
def start_simulation():
    global SIM_MODEL

    pop_limit = request.args.get("limit", None)
    if pop_limit:
        try:
            pop_limit = int(pop_limit)
        except:
            pop_limit = None

    try:
        SIM_MODEL = EvacuationModel(data_dir="data", population_limit=pop_limit)
    except Exception as e:
        return jsonify({"error": f"Model failed to initialize: {str(e)}"}), 500

    return jsonify({
        "status": "started",
        "population": len(SIM_MODEL.person_agents),
        "evac_centers": len(SIM_MODEL.evac_agents)
    })


@app.route("/api/v1/sim/step", methods=["GET"])
def simulation_step():
    global SIM_MODEL
    if SIM_MODEL is None:
        return jsonify({"error": "Simulation not running"}), 400

    SIM_MODEL.step()
    agents = SIM_MODEL.get_agent_positions()

    return jsonify({
        "status": "ok",
        "agents": agents
    })


@app.route("/api/v1/sim/hazard", methods=["GET"])
def hazard_polygon():
    global SIM_MODEL
    if SIM_MODEL is None:
        return jsonify({"error": "Simulation not running"}), 400

    try:
        return jsonify(SIM_MODEL.hazard.to_geojson())
    except:
        return jsonify({"type": "FeatureCollection", "features": []})


@app.route("/api/v1/sim/stats", methods=["GET"])
def simulation_stats():
    global SIM_MODEL
    if SIM_MODEL is None:
        return jsonify({"error": "Simulation not running"}), 400

    hazard_summary = SIM_MODEL.hazard.summary()

    num_safe = sum(1 for p in SIM_MODEL.person_agents.values() if p.state == "safe")
    num_stuck = sum(1 for p in SIM_MODEL.person_agents.values() if p.state == "stuck")
    num_overtaken = sum(1 for p in SIM_MODEL.person_agents.values() if p.state == "overtaken")

    return jsonify({
        "time": SIM_MODEL.sim_time,
        "population": len(SIM_MODEL.person_agents),
        "num_safe": num_safe,
        "num_stuck": num_stuck,
        "num_overtaken": num_overtaken,
        "hazard": hazard_summary
    })


# ============================================================
# CONGESTION LAYER
# ============================================================
@app.route("/api/v1/sim/congestion", methods=["GET"])
def congestion_geojson():
    global SIM_MODEL
    if SIM_MODEL is None:
        return jsonify({"error": "Simulation not running"}), 400

    cong = getattr(SIM_MODEL, "congestion", None)
    if cong is None:
        return jsonify({"type": "FeatureCollection", "features": []})

    features = []
    seg_data = getattr(cong, "segment_data", {})

    for key, meta in seg_data.items():
        try:
            a, b = key  # each node is (lon,lat)
        except:
            continue

        coords = [[a[0], a[1]], [b[0], b[1]]]

        capacity = meta.get("capacity", 1)
        density = meta.get("density", 0)
        intensity = min(1.0, density / capacity) if capacity > 0 else 0

        feat = {
            "type": "Feature",
            "properties": {
                "capacity": capacity,
                "density": density,
                "intensity": intensity
            },
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            }
        }
        features.append(feat)

    return jsonify({"type": "FeatureCollection", "features": features})


# ============================================================
# VEHICLE POSITIONS
# ============================================================
@app.route("/api/v1/sim/vehicles", methods=["GET"])
def vehicles_positions():
    global SIM_MODEL

    if SIM_MODEL is None:
        return jsonify({"error": "Simulation not running"}), 400

    vehicles = []
    vehicle_dict = getattr(SIM_MODEL, "vehicle_agents", {}) or {}

    for vid, v in vehicle_dict.items():
        try:
            lat, lon = v.get_latlon()
        except:
            lon, lat = v.pos

        vehicles.append({
            "id": vid,
            "lat": lat,
            "lon": lon,
            "state": getattr(v, "state", "vehicle")
        })

    return jsonify({"vehicles": vehicles})


# ============================================================
# STOP SIMULATION
# ============================================================
@app.route("/api/v1/sim/stop", methods=["GET", "POST"])
def stop_simulation():
    global SIM_MODEL
    SIM_MODEL = None
    return jsonify({"status": "stopped"})


# ============================================================
# RUN SERVER
# ============================================================
if __name__ == "__main__":
    app.run(debug=True)
