import threading
import time
import json
from flask import Flask, render_template, request, jsonify, send_from_directory # type: ignore
from flask_cors import CORS # type: ignore
from pythonosc import udp_client, dispatcher, osc_server # type: ignore
from PIL import ImageGrab, Image # type: ignore
import os
import base64
from io import BytesIO
import logging
import socket
import subprocess

# suppress all logging messages to error level
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# Flask app setup
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# get local ip
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't have to be reachable
        s.connect(("10.254.254.254", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

LOCAL_IP = get_local_ip()
print(f"Local IP: {LOCAL_IP}")

# OSC Client setup
QLAB_IP = LOCAL_IP  # Default QLab IP
QLAB_PORT = 53000  # Default QLab OSC port
OSC_LISTEN_PORT = 53001  # Port to listen for responses from QLab

osc_client = udp_client.SimpleUDPClient(QLAB_IP, QLAB_PORT)

# OSC Dispatcher setup
osc_dispatcher = dispatcher.Dispatcher()

# Global variables
detected_devices = [{"id": QLAB_IP, "name": "Main QLab"}]
workspaces = []
selected_device = None
selected_workspace = None
selected_cue_number = "N/A"
selected_cue_name = "N/A"
active_cue_number = "N/A"
active_cue_name = "N/A"

# Handle workspace response
def handle_workspace_response(_, *args):
    global workspaces
    # print(f"Raw workspace response: {args}")
    try:
        if len(args) >= 1:
            response = json.loads(args[0])
            if "data" in response:
                workspaces = response["data"]
                # print(f"Updated workspaces: {workspaces}")
                return
    except Exception as e:
        print(f"Error parsing workspace response: {e}")
    workspaces = []  # Reset if the response is invalid

# Register OSC handlers
osc_dispatcher.map("/reply/workspaces", handle_workspace_response)

# Start OSC server
def start_osc_server():
    server = osc_server.ThreadingOSCUDPServer(("0.0.0.0", OSC_LISTEN_PORT), osc_dispatcher)
    # print(f"OSC Server listening on port {OSC_LISTEN_PORT}")
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

start_osc_server()

# Flask routes
@app.route("/")
def index():
    return render_template(
        "index.html",
        devices=detected_devices,
        workspaces=workspaces,
        selected_workspace=selected_workspace,
    )

@app.route("/fetch_workspaces", methods=["POST"])
def fetch_workspaces():
    global selected_device, workspaces
    selected_device = request.form.get("device_id")
    if selected_device:
        try:
            osc_client.send_message("/workspaces", [])
            # print(f"Sent request for workspaces for device: {selected_device}")
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
    # print(f"Selected workspace set to: {selected_workspace}")
    if selected_workspace:
        try:
            osc_client.send_message("/workspace/connect", [selected_workspace])
            return jsonify({"status": "success", "workspace": selected_workspace, "message": "Workspace connected successfully"})
        except Exception as e:
            print(f"Error connecting to workspace: {e}")
            return jsonify({"status": "error", "message": "Failed to connect to workspace"}), 500
    return jsonify({"status": "error", "message": "No workspace selected"}), 400

@app.route("/button_action", methods=["POST"])
def button_action():
    try:
        # print("Received POST data:", request.form)
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
                # print(f"Sent OSC command: {osc_command}")
                return jsonify({"status": "success", "action": action})
            else:
                return jsonify({"status": "error", "message": "Unknown action"}), 400
        else:
            return jsonify({"status": "error", "message": "No workspace selected"}), 400
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"status": "error", "message": "Something went wrong"}), 500

# Periodically fetch current and selected cues
def fetch_current_cue_periodically():
    global selected_cue_number, selected_cue_name, active_cue_number, active_cue_name

    # Initialize these to None so we know when they haven't been set yet.
    selected_cue_number = None
    selected_cue_name = None
    active_cue_number = None
    active_cue_name = None

    while True:
        try:
            # Fetch the selected cue number
            current_selected_cue_number_script = """
            tell application id "com.figure53.QLab.4"
                tell front workspace
                    try
                        set selectedCue to first item of (selected as list)
                        return name of selectedCue
                    on error
                        return "N/A"
                    end try
                end tell
            end tell
            """
            result = subprocess.run(
                ["osascript", "-e", current_selected_cue_number_script],
                capture_output=True,
                text=True,
                check=True
            )
            cue_number = result.stdout.strip()
            if cue_number != "N/A":
                selected_cue_number = cue_number

            # Fetch the selected cue name
            selected_cue_name_script = """
            tell application id "com.figure53.QLab.4"
                tell front workspace
                    try
                        set selectedCue to first item of (selected as list)
                        return q display name of selectedCue
                    on error
                        return "N/A"
                    end try
                end tell
            end tell
            """
            result = subprocess.run(
                ["osascript", "-e", selected_cue_name_script],
                capture_output=True,
                text=True,
                check=True
            )
            cue_name = result.stdout.strip()
            if cue_name != "N/A":
                selected_cue_name = cue_name

            # Fetch the active cue number
            active_cue_number_script = """
            tell application id "com.figure53.QLab.4"
                tell front workspace
                    try
                        set activeCue to last item of (active cues as list)
                        return q number of activeCue
                    on error
                        return "N/A"
                    end try
                end tell
            end tell
            """
            result = subprocess.run(
                ["osascript", "-e", active_cue_number_script],
                capture_output=True,
                text=True,
                check=True
            )
            cue_number = result.stdout.strip()
            if cue_number != "N/A":
                active_cue_number = cue_number

            # Fetch the active cue name
            active_cue_name_script = """
            tell application id "com.figure53.QLab.4"
                tell front workspace
                    try
                        set activeCue to last item of (active cues as list)
                        return q display name of activeCue
                    on error
                        return "N/A"
                    end try
                end tell
            end tell
            """
            result = subprocess.run(
                ["osascript", "-e", active_cue_name_script],
                capture_output=True,
                text=True,
                check=True
            )
            cue_name = result.stdout.strip()
            if cue_name != "N/A":
                active_cue_name = cue_name

        except subprocess.CalledProcessError as e:
            print(f"Error: {e.stderr.strip()}")

        # Log current values (debugging purposes)
        print(f"Selected cue: {selected_cue_number} - {selected_cue_name}")
        print(f"Active cue: {active_cue_number} - {active_cue_name}")

        # Wait before checking again
        time.sleep(0.25)


# Start the periodic task in a separate thread
fetch_current_cue_thread = threading.Thread(target=fetch_current_cue_periodically, daemon=True)
fetch_current_cue_thread.start()

# Ensure the "static" directory exists
if not os.path.exists('static'):
    os.makedirs('static')

def capture_screenshot():
    # Grab a screenshot of window with title "QLab" and resize it to 720x480 and get the base64 string

    while True:
        # Get the screenshot of the window with title "QLab"
        os.system("screencapture -l$(osascript -e 'tell app \"QLab\" to id of window 1') -x static/screenshot.png")

        # Open the screenshot image
        screenshot = Image.open("static/screenshot.png")

        # Resize the image to 720x480
        screenshot = screenshot.resize((720, 480))

        # Save the resized image
        screenshot.save("static/screenshot.png")

        # Save the base64 string of the image
        with open("static/screenshot.png", "rb") as img_file:
            img_base64 = base64.b64encode(img_file.read()).decode("utf-8")

        # Store the base64 string in a global variable
        global current_screenshot
        current_screenshot = img_base64

        # print("Screenshot captured and encoded.")
        time.sleep(0.25)
        
    


    # while True:
    #     screenshot = ImageGrab.grab()
    #     screenshot = screenshot.resize((720, 480))
        
    # #     # Save screenshot to a BytesIO object
    #     buffered = BytesIO()
    #     screenshot.save(buffered, format="PNG")
        
    #     # Encode the image as a base64 string
    #     img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
    #     # Store the base64 string in a global variable
    #     global current_screenshot
    #     current_screenshot = img_base64
        
    #     # print("Screenshot captured and encoded.")
    #     time.sleep(0.5)

# Initialize a global variable to hold the base64 string
current_screenshot = ""

# Start the screenshot capture thread
screenshot_thread = threading.Thread(target=capture_screenshot, daemon=True)
screenshot_thread.start()

@app.route("/screenshot", methods=["GET"])
def get_screenshot():
    if current_screenshot:
        return jsonify({"screenshot": current_screenshot})
    return jsonify({"status": "error", "message": "No screenshot available"}), 400

# Cue information endpoint
@app.route("/cue_info", methods=["GET"])
def cue_info():
    global selected_cue_number, selected_cue_name, active_cue_number, active_cue_name
    return jsonify({
        "selected_cue_number": selected_cue_number,
        "selected_cue_name": selected_cue_name,
        "active_cue_number": active_cue_number,
        "active_cue_name": active_cue_name,
    })


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000, use_reloader=False, threaded=True)
