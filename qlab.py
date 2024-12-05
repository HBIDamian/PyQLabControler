import threading
import time
import json
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from pythonosc import udp_client, dispatcher, osc_server

# Flask app setup
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# OSC Client setup
QLAB_IP = "192.168.1.107"  # Change to your QLab device IP
QLAB_PORT = 53000  # Default QLab OSC port
OSC_LISTEN_PORT = 53001  # Port to listen for responses from QLab

osc_client = udp_client.SimpleUDPClient(QLAB_IP, QLAB_PORT)
osc_client.send_message("/alwaysReply", 1)
osc_client.send_message("/version", 1)

# OSC Dispatcher setup
osc_dispatcher = dispatcher.Dispatcher()

# Global variables
detected_devices = [{"id": QLAB_IP, "name": "Main QLab"}]
workspaces = []
selected_device = None
selected_workspace = None
current_cue = {"number": "N/A", "name": "No Cue Selected"}
selected_cue = {"number": "N/A", "name": "No Cue Selected"}
current_audio_levels = {"master": 1.0, "left": 1.0, "right": 1.0}

# Handle workspace response
def handle_workspace_response(_, *args):
    global workspaces
    print(f"Raw workspace response: {args}")
    try:
        if len(args) >= 1:
            response = json.loads(args[0])
            if "data" in response:
                workspaces = response["data"]
                print(f"Updated workspaces: {workspaces}")
                return
    except Exception as e:
        print(f"Error parsing workspace response: {e}")
    workspaces = []  # Reset if the response is invalid

# Handle current cue response
def handle_current_cue_response(_, *args):
    global current_cue, selected_cue
    print(f"Raw current cue response: {args}")
    try:
        if args and isinstance(args[0], str):
            cue_data = json.loads(args[0])
            if isinstance(cue_data, dict) and "data" in cue_data:
                cue = cue_data["data"]
                current_cue = {"number": cue.get("number", "N/A"), "name": cue.get("name", "No Cue Selected")}
                selected_cue = current_cue
                print(f"Updated current cue: {current_cue}")
                return
        elif args and isinstance(args[0], dict):
            cue = args[0]
            current_cue = {"number": cue.get("number", "N/A"), "name": cue.get("name", "No Cue Selected")}
            selected_cue = current_cue
            print(f"Updated current cue: {current_cue}")
            return
    except Exception as e:
        print(f"Error parsing cue response: {e}")

# Handle audio levels response
def handle_audio_levels_response(_, *args):
    global current_audio_levels
    if len(args) >= 3:
        try:
            master, left, right = map(float, args[:3])
            current_audio_levels = {"master": master, "left": left, "right": right}
            print(f"Audio Levels - Master: {master}, Left: {left}, Right: {right}")
        except ValueError:
            print("Invalid audio level values received:", args)
    else:
        print("Error in audio levels response:", args)

# Register OSC handlers
osc_dispatcher.map("/reply/workspaces", handle_workspace_response)
osc_dispatcher.map("/reply/currentCue", handle_current_cue_response)
osc_dispatcher.map("/reply/audioLevels", handle_audio_levels_response)

# Start OSC server
def start_osc_server():
    server = osc_server.ThreadingOSCUDPServer(("0.0.0.0", OSC_LISTEN_PORT), osc_dispatcher)
    print(f"OSC Server listening on port {OSC_LISTEN_PORT}")
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

start_osc_server()

# Flask routes
@app.route("/")
def index():
    return render_template(
        "index.html",
        devices=detected_devices,
        current_cue=current_cue,
        selected_cue=selected_cue,
        cue_list=[],  # Add the cue_list here, initially empty        
        workspaces=workspaces,
        selected_workspace=selected_workspace,
        audio_levels=current_audio_levels,
    )

@app.route("/fetch_workspaces", methods=["POST"])
def fetch_workspaces():
    global selected_device, workspaces
    selected_device = request.form.get("device_id")
    if selected_device:
        try:
            osc_client.send_message("/workspaces", [])
            print(f"Sent request for workspaces for device: {selected_device}")
            return jsonify({"status": "success", "message": "Fetching workspaces"})
        except Exception as e:
            print(f"Error fetching workspaces: {e}")
            return jsonify({"status": "error", "message": "Failed to fetch workspaces"}), 500
    return jsonify({"status": "error", "message": "No device selected"}), 400

@app.route("/current_workspaces", methods=["GET"])
def current_workspaces():
    global workspaces
    if workspaces:
        return jsonify({"workspaces": workspaces})
    print("No workspaces available")
    return jsonify({"status": "error", "message": "No workspaces available"}), 400

@app.route("/select_workspace", methods=["POST"])
def select_workspace():
    global selected_workspace
    selected_workspace = request.form.get("workspace_id")
    print(f"Selected workspace set to: {selected_workspace}")
    if selected_workspace:
        try:
            osc_client.send_message("/workspace/connect", [selected_workspace])
            return jsonify({"status": "success", "workspace": selected_workspace, "message": "Workspace connected successfully"})
        except Exception as e:
            print(f"Error connecting to workspace: {e}")
            return jsonify({"status": "error", "message": "Failed to connect to workspace"}), 500
    return jsonify({"status": "error", "message": "No workspace selected"}), 400

@app.route("/cue/active", methods=["GET"])
def get_active_cue():
    try:
        cue_data = {"number": current_cue["number"], "name": current_cue["name"]}
        print(f"Active cue: {cue_data}")
        return jsonify(cue_data)
    except Exception as e:
        print(f"Error fetching active cue: {e}")
        return jsonify({"error": "Failed to fetch active cue"}), 500

@app.route("/cue/selected", methods=["GET"])
def get_selected_cue():
    try:
        cue_data = {"number": selected_cue["number"], "name": selected_cue["name"]}
        print(f"Selected cue: {cue_data}")
        return jsonify(cue_data)
    except Exception as e:
        print(f"Error fetching selected cue: {e}")
        return jsonify({"error": "Failed to fetch selected cue"}), 500

@app.route("/button_action", methods=["POST"])
def button_action():
    try:
        print("Received POST data:", request.form)
        action = request.form.get("data-action")
        if selected_workspace:
            osc_command = {
                "go": f"/workspace/{selected_workspace}/go",
                "next": f"/workspace/{selected_workspace}/select/next",
                "previous": f"/workspace/{selected_workspace}/select/previous",
                "panic": f"/workspace/{selected_workspace}/panic",
                "pause": f"/workspace/{selected_workspace}/pause",
                "resume": f"/workspace/{selected_workspace}/resume",
            }.get(action)

            if osc_command:
                osc_client.send_message(osc_command, [])
                print(f"Sent OSC command: {osc_command}")
                return jsonify({"status": "success", "action": action})
            else:
                return jsonify({"status": "error", "message": "Unknown action"}), 400
        else:
            return jsonify({"status": "error", "message": "No workspace selected"}), 400
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"status": "error", "message": "Something went wrong"}), 500

@app.route("/set_audio", methods=["POST"])
def set_audio():
    master = float(request.form.get("master", 1.0))
    left = float(request.form.get("left", 1.0))
    right = float(request.form.get("right", 1.0))
    if selected_device:
        osc_client.send_message("/audio/master", master)
        osc_client.send_message("/audio/left", left)
        osc_client.send_message("/audio/right", right)
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "No device selected"}), 400

@app.route("/audio_levels", methods=["GET"])
def audio_levels():
    if selected_device:
        osc_client.send_message("/audio/get", [])
        return jsonify(current_audio_levels)
    return jsonify({"master": 1.0, "left": 1.0, "right": 1.0}), 400

# Periodically fetch current and selected cues
def fetch_current_cue_periodically():
    while True:
        if selected_workspace:
            print(f"Fetching current and selected cue for workspace: {selected_workspace}")
            osc_client.send_message(f"/workspace/{selected_workspace}/playbackPosition", [])
            osc_client.send_message(f"/workspace/{selected_workspace}/selectedCues", [])
        time.sleep(2)

# Start the periodic task in a separate thread
fetch_current_cue_thread = threading.Thread(target=fetch_current_cue_periodically, daemon=True)
fetch_current_cue_thread.start()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False, threaded=True)
