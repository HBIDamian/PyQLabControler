"""
qLabRemote.py - A web-based remote control for QLab
"""

# Standard library imports
import os
import socket
import threading
import time
import json
import webbrowser
import logging
from typing import Dict, List, Any, Optional, Union
from ScriptingBridge import SBApplication
from Foundation import NSAppleScript, NSAppleEventDescriptor
import platform

# Third-party imports
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO

# Configuration
WEB_PORT = 5000

# Logging configuration
# Options: logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL
LOG_LEVEL = logging.ERROR  # Change this to control verbosity of logs
LOG_TO_FILE = False         # Toggle logging to file on/off
LOG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'qlab_remote.log')

# Configure logging
handlers = [logging.StreamHandler()]
if LOG_TO_FILE:
    handlers.append(logging.FileHandler(LOG_FILE_PATH))

logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=handlers
)
logger = logging.getLogger('qLabRemote')

# Suppress Flask and Werkzeug HTTP request logs
logging.getLogger('werkzeug').setLevel(logging.WARNING)  # Only show WARNING and above
logging.getLogger('flask').setLevel(logging.WARNING)     # Only show WARNING and above
logging.getLogger('socketio').setLevel(logging.WARNING)  # Only show WARNING and above
logging.getLogger('engineio').setLevel(logging.WARNING)  # Only show WARNING and above

# Flask app setup
app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching
app.config['SECRET_KEY'] = 'qlab-remote-secret-key'
socketio = SocketIO(app)

discovered_instances: List[Dict[str, Any]] = []
current_instance: Optional[Dict[str, Any]] = None
current_selected_cue: Dict[str, str] = {"number": "", "name": ""}
next_cue: Dict[str, str] = {"number": "", "name": ""}
event_history: List[Dict[str, Any]] = []  # Store recent events
max_event_history = 50  # Maximum number of events to keep

# serialize all AppleScript calls (they aren’t thread‐safe)
script_lock = threading.Lock()

# Performance tracking
commands_sent = 0
error_count = 0
total_latency_ms = 0.0

# New AppleScript-based QLab client
class QLabAppleScriptClient:
    """AppleScript-based client for controlling QLab"""
    
    def __init__(self, bundle_id="com.figure53.QLab.4"):
        self.bundle_id = bundle_id
        self.qlab = SBApplication.applicationWithBundleIdentifier_(bundle_id)
        
    def is_running(self):
        """Check if QLab is running"""
        return self.qlab.isRunning()
    
    def run_apple_script(self, script_text):
        """Run an AppleScript and return the result"""
        logger.debug(f"Running AppleScript: {script_text[:100]}...")
        script = NSAppleScript.alloc().initWithSource_(script_text)
        
        # acquire lock around the actual execution
        with script_lock:
            result, error = script.executeAndReturnError_(None)
        
        if error:
            logger.error(f"AppleScript error: {error}")
            return None, error
        
        return result, None
    
    def get_workspaces(self):
        """Get a list of open workspaces in QLab"""
        script_text = f"""
        tell application id "{self.bundle_id}"
            try
                set wsNames to name of workspaces
                set wsIDs to id of workspaces

                set result to {{}}
                repeat with i from 1 to count of wsNames
                    set end of result to {{wsNames's item i, wsIDs's item i}}
                end repeat

                return result
            on error errMessage
                return {{"Error: " & errMessage}}
            end try
        end tell
        """
        
        result, error = self.run_apple_script(script_text)
        
        if error:
            logger.error(f"Error getting workspaces: {error}")
            return []
        
        workspaces = []
        if result:
            for i in range(result.numberOfItems()):
                workspace_data = result.descriptorAtIndex_(i+1)
                if workspace_data and workspace_data.numberOfItems() >= 2:
                    name = workspace_data.descriptorAtIndex_(1).stringValue()
                    workspace_id = workspace_data.descriptorAtIndex_(2).stringValue()
                    workspaces.append({
                        "name": name,
                        "id": workspace_id
                    })
        
        return workspaces
    
    def set_active_workspace(self, workspace_id):
        """Set the active workspace by ID"""
        script_text = f"""
        tell application id "{self.bundle_id}"
            try
                repeat with w in workspaces
                    if id of w is "{workspace_id}" then
                        set w to active workspace
                        return true
                    end if
                end repeat
                return false
            on error errMessage
                return false
            end try
        end tell
        """
        
        result, error = self.run_apple_script(script_text)
        
        if error:
            logger.error(f"Error setting active workspace: {error}")
            return False
        
        if result and result.booleanValue():
            return True
            
        return False
    
    def get_selected_cue(self):
        """Get information about the currently selected cue (includes uniqueID)."""
        script_text = f"""
        tell application id "{self.bundle_id}"
            try
                set sel to selected of front workspace
                if length of sel > 0 then
                    set c to last item of sel
                    return {{uniqueID of c, q number of c, q display name of c, q type of c}}
                else
                    return {{"No selection", "", "", ""}}
                end if
            on error errMsg
                return {{"Error", errMsg, "", ""}}
            end try
        end tell
        """
        result, error = self.run_apple_script(script_text)
        if error:
            return {"error": str(error)}
        if result and result.numberOfItems() >= 4:
            return {
                "id":     result.descriptorAtIndex_(1).stringValue(),
                "number": result.descriptorAtIndex_(2).stringValue(),
                "name":   result.descriptorAtIndex_(3).stringValue(),
                "type":   result.descriptorAtIndex_(4).stringValue(),
            }
        return {"number": "--", "name": "No selection", "type": "unknown"}
    
    def get_next_cue(self):
        """Get next cue by flattening all cues in Python and finding the selected cue's successor."""
        selected = self.get_selected_cue()
        if "error" in selected or not selected.get("id"):
            return {"number": "--", "name": "No next cue", "type": "unknown"}
        cues = self.get_all_cues()
        for idx, cue in enumerate(cues):
            if cue.get("id") == selected["id"]:
                if idx + 1 < len(cues):
                    nxt = cues[idx + 1]
                    return {
                        "number": nxt.get("number", "(no number)"),
                        "name":   nxt.get("originalName", nxt.get("name", "Unnamed")),
                        "type":   "unknown"
                    }
                break
        return {"number": "--", "name": "No next cue", "type": "unknown"}
    
    def get_active_cue(self):
        """Get information about any currently active cue"""
        script_text = f"""
        tell application id "{self.bundle_id}"
            try
                set ws to front workspace
                set active_list to active cues of ws
                if length of active_list > 0 then
                    set c to first item of active_list
                    return {{q number of c, q display name of c, q type of c}}
                else
                    return {{"No active cue", "", ""}}
                end if
            on error errMessage
                return {{"Error: " & errMessage, "", ""}}
            end try
        end tell
        """
        
        result, error = self.run_apple_script(script_text)
        
        if error:
            return {"error": str(error)}
        
        if result and result.numberOfItems() >= 3:
            return {
                "number": result.descriptorAtIndex_(1).stringValue(),
                "name": result.descriptorAtIndex_(2).stringValue(),
                "type": result.descriptorAtIndex_(3).stringValue(),
            }
        
        return {"number": "--", "name": "No active cue", "type": "unknown"}
    
    def go(self):
        """Send GO command to QLab"""
        script_text = f"""
        tell application id "{self.bundle_id}"
            try
                set ws to front workspace
                go ws
                return "ok"
            on error errMessage
                return "Error: " & errMessage
            end try
        end tell
        """
        
        result, error = self.run_apple_script(script_text)
        if error:
            logger.error(f"Error sending GO command: {error}")
            return False
        
        return True
    
    def stop(self):
        """Send stop command to QLab"""
        script_text = f"""
        tell application id "{self.bundle_id}"
            try
                set ws to front workspace
                stop ws
                return "ok"
            on error errMessage
                return "Error: " & errMessage
            end try
        end tell
        """
        
        result, error = self.run_apple_script(script_text)
        if error:
            logger.error(f"Error sending stop command: {error}")
            return False
        
        return True
    
    def panic(self):
        """Send panic command to QLab"""
        script_text = f"""
        tell application id "{self.bundle_id}"
            try
                set ws to front workspace
                panic ws
                return "ok"
            on error errMessage
                return "Error: " & errMessage
            end try
        end tell
        """
        
        result, error = self.run_apple_script(script_text)
        if error:
            logger.error(f"Error sending panic command: {error}")
            return False
        
        return True
    
    def reset(self):
        """Reset workspace"""
        script_text = f"""
        tell application id "{self.bundle_id}"
            try
                set ws to front workspace
                reset ws
                return "ok"
            on error errMessage
                return "Error: " & errMessage
            end try
        end tell
        """
        
        result, error = self.run_apple_script(script_text)
        if error:
            logger.error(f"Error sending reset command: {error}")
            return False
        
        return True
    
    def next(self):
        """Move playhead to the next cue"""
        script_text = f"""
        tell application id "{self.bundle_id}"
            try
                set ws to front workspace
                movePlayheadDown ws
                return "ok"
            on error errMessage
                return "Error: " & errMessage
            end try
        end tell
        """
        
        result, error = self.run_apple_script(script_text)
        if error:
            logger.error(f"Error sending next command: {error}")
            return False
        
        return True
    
    def previous(self):
        """Move playhead to the previous cue"""
        script_text = f"""
        tell application id "{self.bundle_id}"
            try
                set ws to front workspace
                movePlayheadUp ws
                return "ok"
            on error errMessage
                return "Error: " & errMessage
            end try
        end tell
        """
        
        result, error = self.run_apple_script(script_text)
        if error:
            logger.error(f"Error sending previous command: {error}")
            return False
        
        return True

    def get_all_cues(self):
        """Return list of all cues in front workspace as [{'id':..., 'number':..., 'name':...}, ...]"""
        # Modified AppleScript to get ALL cues including those within groups
        script_text = f"""
        tell application id "{self.bundle_id}"
            try
                set ws to front workspace
                set allCues to {{}}
                set mainList to first cue list of ws
                
                -- Function to process a list of cues, including those in groups
                -- We're using a list-based approach to avoid AppleScript handler limitations
                
                -- First add all top-level cues
                set theCues to cues of mainList
                repeat with i from 1 to count of theCues
                    set c to item i of theCues
                    -- Include ALL cues, even those without numbers
                    set end of allCues to {{uniqueID of c, q number of c, q display name of c, i, 0}} -- 0 is the depth level
                    
                    -- If this is a group cue, we need to process its children
                    if q type of c is "Group" then
                        -- Next, we'll check for cues within this group
                        set groupCues to cues of c
                        repeat with j from 1 to count of groupCues
                            set gc to item j of groupCues
                            -- Add this cue with a depth of 1 (nested once)
                            set end of allCues to {{uniqueID of gc, q number of gc, q display name of gc, (i * 100 + j), 1}}
                            
                            -- If this is also a group (nested group), process its children too
                            if q type of gc is "Group" then
                                set nestedCues to cues of gc
                                repeat with k from 1 to count of nestedCues
                                    set nc to item k of nestedCues
                                    -- Add with depth of 2 (nested twice)
                                    set end of allCues to {{uniqueID of nc, q number of nc, q display name of nc, (i * 10000 + j * 100 + k), 2}}
                                end repeat
                            end if
                        end repeat
                    end if
                end repeat
                
                return allCues
            on error errMsg
                log "Error getting cues: " & errMsg
                return {{}}
            end try
        end tell
        """
        
        logger.info("Fetching all cues including nested groups...")
        result, error = self.run_apple_script(script_text)
        
        cues = []
        if error:
            logger.error(f"AppleScript error: {error}")
            return cues
            
        if result:
            count = result.numberOfItems()
            logger.info(f"Retrieved {count} cues from QLab (including nested)")
            
            for i in range(count):
                d = result.descriptorAtIndex_(i+1)
                if d and d.numberOfItems() >= 5:  # Now includes depth level
                    cue_id = d.descriptorAtIndex_(1).stringValue()
                    cue_number = d.descriptorAtIndex_(2).stringValue()
                    cue_name = d.descriptorAtIndex_(3).stringValue()
                    cue_index = d.descriptorAtIndex_(4).int32Value()
                    cue_depth = d.descriptorAtIndex_(5).int32Value()
                    
                    # Only check for empty ID
                    if cue_id:
                        # Add indentation based on depth level
                        prefix = "--> " * cue_depth
                        display_name = f"{prefix}{cue_name or 'Unnamed'}"
                        
                        cues.append({
                            "id": cue_id,
                            "number": cue_number or "(no number)",  # Provide placeholder for empty numbers
                            "name": display_name,
                            "originalName": cue_name or "Unnamed",  # Store original name without prefix
                            "originalIndex": cue_index,  # Store original position
                            "depth": cue_depth  # Store the depth level
                        })
                        logger.debug(f"Added cue {cue_index}: {cue_number or '(no number)'} - {display_name} (ID: {cue_id}, depth: {cue_depth})")
        
        logger.info(f"Returning {len(cues)} valid cues in hierarchical order")
        return cues

# QLabClientWrapper class to handle client functionality using AppleScript
class QLabClientWrapper:
    def __init__(self, workspace_id=None):
        self.client = QLabAppleScriptClient()
        self.current_selected_cue = {"number": "N/A", "name": "Unnamed"}
        self.next_cue = {"number": "N/A", "name": "Unnamed"}
        self.workspace_id = workspace_id or "applescript"
        self.connected = False
        self.connection_error = None
        
        try:
            # Check if QLab is running
            if self.client.is_running():
                self.connected = True
                self.connection_error = None
                
                # If workspace_id is provided, set it as the active workspace
                if workspace_id and workspace_id != "applescript":
                    if self.client.set_active_workspace(workspace_id):
                        logger.info(f"Set active workspace to ID: {workspace_id}")
                    else:
                        logger.warning(f"Could not set workspace ID: {workspace_id}")
                
                logger.info("Successfully connected to QLab via AppleScript")
                self.update_cue_info()
            else:
                self.connection_error = "QLab is not running"
                logger.error(self.connection_error)
        except Exception as e:
            logger.error(f"Error setting up QLab client: {e}")
            self.connection_error = f"Error: {e}"

    def update_cue_info(self):
        """Update current and next cue info with detailed logging and refined logic."""
        try:
            # Get the currently selected cue
            selected_cue = self.client.get_selected_cue()
            logger.debug(f"Selected cue info: {selected_cue}")
            
            # If we have a valid selected cue, use it as current selected cue
            if selected_cue and "error" not in selected_cue:
                self.current_selected_cue = selected_cue
                logger.debug(f"Current selected cue set from selection: {self.current_selected_cue}")
                
                # Also get the next cue in the sequence
                next_cue_info = self.client.get_next_cue()
                if next_cue_info and "error" not in next_cue_info:
                    self.next_cue = next_cue_info
                    logger.debug(f"Next cue set from sequence: {self.next_cue}")
                else:
                    self.next_cue = {"number": "--", "name": "No next cue", "type": "unknown"}
            else:
                # If no selection, try to use active cue as current
                active_cue = self.client.get_active_cue()
                if active_cue and "error" not in active_cue:
                    self.current_selected_cue = active_cue
                    logger.debug(f"Current selected cue set from active: {self.current_selected_cue}")
                    # No reliable way to get next cue after an active cue without knowing context
                    self.next_cue = {"number": "--", "name": "No next cue", "type": "unknown"}
                else:
                    self.current_selected_cue = {"number": "--", "name": "No selection or active cue", "type": "unknown"}
                    self.next_cue = {"number": "--", "name": "No next cue", "type": "unknown"}
            
        except Exception as e:
            logger.error(f"Error in update_cue_info: {e}", exc_info=True)
            self.current_selected_cue = {"number": "ERR", "name": f"Error: {str(e)[:30]}", "type": "error"}
            self.next_cue = {"number": "ERR", "name": "Error", "type": "error"}

    def get_current_selected_cue(self):
        """Get the current selected cue info"""
        self.update_cue_info()
        return self.current_selected_cue
        
    def get_next_cue(self):
        """Get the next cue info"""
        self.update_cue_info()
        return self.next_cue

    def play(self):
        """Send play (GO) command to QLab"""
        try:
            success = self.client.go()
            if success:
                # Update cue info after a small delay
                self.update_cue_info()
                return True
            else:
                logger.error("Error sending play command")
                return False
        except Exception as e:
            logger.error(f"Error sending play command: {e}")
            return False
            
    def stop(self):
        """Send stop command to QLab"""
        try:
            success = self.client.stop()
            if success:
                threading.Timer(0, self.update_cue_info).start()
                return success
            return False
        except Exception as e:
            logger.error(f"Error sending stop command: {e}")
            return False
            
    def next(self):
        """Select next cue"""
        try:
            success = self.client.next()
            if success:
                # Update cue info after a short delay
                threading.Timer(0, self.update_cue_info).start()
                return True
            else:
                logger.error(f"Error sending next command")
                return False
        except Exception as e:
            logger.error(f"Error sending next command: {e}")
            return False
            
    def previous(self):
        """Select previous cue"""
        try:
            success = self.client.previous()
            if success:
                # Update cue info after a short delay
                threading.Timer(0, self.update_cue_info).start()
                return True
            else:
                logger.error(f"Error sending previous command")
                return False
        except Exception as e:
            logger.error(f"Error sending previous command: {e}")
            return False
            
    def panic(self):
        """Send panic command to QLab"""
        try:
            success = self.client.panic()
            return success
        except Exception as e:
            logger.error(f"Error sending panic command: {e}")
            return False

    def reset(self):
        """Reset workspace"""
        try:
            success = self.client.reset()
            if success:
                # Update cue info after a short delay
                threading.Timer(0, self.update_cue_info).start()
                return True
            else:
                logger.error(f"Error sending reset command")
                return False
        except Exception as e:
            logger.error(f"Error sending reset command: {e}")
            return False

    def skip_to_cue(self, cue_number):
        """Load and go to specified cue number"""
        script_text = f"""
        tell application id "{self.client.bundle_id}"
            try
                set ws to front workspace
                load (cue id "{cue_number}")
                go ws
                return "ok"
            on error errMessage
                return "Error: " & errMessage
            end try
        end tell
        """
        result, error = self.client.run_apple_script(script_text)
        if error:
            logger.error(f"Error skipping to cue {cue_number}: {error}")
            return False
        return True

    def cleanup(self):
        """Clean up resources"""
        pass

    def get_all_cues(self):
        return self.client.get_all_cues()

# Flask routes
@app.route('/')
def index():
    # Get local IP address more reliably
    local_ip = 'Unknown'
    try:
        # Create a temporary socket connection to determine which interface would be used
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # This doesn't actually establish a connection
        s.connect(('8.8.8.8', 1))
        local_ip = s.getsockname()[0]
        s.close()
    except:
        # Fallback method if the above fails
        try:
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
        except:
            local_ip = 'Unknown'
    python_version = platform.python_version()
    return render_template(
        'index.html',
        local_ip=local_ip,
        python_version=python_version,
        port=WEB_PORT
    )

@app.route('/api/instances', methods=['GET'])
def get_instances():
    try:
        instances, error = discover_qlab_instances()
        if error:
            return jsonify({"success": False, "error": error})
        return jsonify({"success": True, "instances": instances})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/refresh_instances', methods=['POST'])
def refresh_instances():
    try:
        instances, error = discover_qlab_instances()
        if error:
            return jsonify({"success": False, "error": error})
        return jsonify({
            "success": True,
            "instances": instances,
            "message": f"Found {len(instances)} workspace(s)"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/connect/<int:instance_id>', methods=['POST'])
def connect_to_instance(instance_id):
    """Connect to a specific QLab instance by ID"""
    global current_instance, current_selected_cue, next_cue
    
    if instance_id < 0 or instance_id >= len(discovered_instances):
        return jsonify({"success": False, "error": "Invalid instance ID"})
    
    # Disconnect from any existing connection
    if current_instance and 'client' in current_instance:
        try:
            current_instance['client'].cleanup()
        except:
            pass  # Ignore errors during cleanup
    
    try:
        instance = discovered_instances[instance_id]
        logger.info(f"Connecting to QLab workspace: {instance['name']} (ID: {instance.get('workspace_id', 'default')})")
        
        # Create new client connection using AppleScript
        client = QLabClientWrapper(instance.get('workspace_id'))
        
        # Verify connection was successful
        if client.connection_error:
            return jsonify({"success": False, "error": client.connection_error})
        
        if not client.connected:
            return jsonify({"success": False, "error": "Failed to connect to QLab"})
        
        # Store the client
        current_instance = {
            "client": client,
            "info": instance
        }
        
        # Get initial cue data
        current_selected_cue = client.get_current_selected_cue()
        next_cue = client.get_next_cue()
        
        logger.info(f"Successfully connected to {instance['name']}")
        return jsonify({
            "success": True,
            "name": instance["name"],
            "ip": instance.get("ip", "localhost"),
            "workspace_id": instance.get("workspace_id", "default"),
            "currentCue": current_selected_cue,
            "nextCue": next_cue
        })
    except Exception as e:
        logger.error(f"Error connecting to QLab instance: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/disconnect', methods=['POST'])
def disconnect():
    """Disconnect from the current QLab instance"""
    global current_instance, current_selected_cue, next_cue
    
    if current_instance and 'client' in current_instance:
        try:
            logger.info("Disconnecting from QLab")
            current_instance['client'].cleanup()
        except Exception as e:
            logger.error(f"Error during disconnect: {e}")
        
    current_instance = None
    current_selected_cue = {"number": "", "name": ""}
    next_cue = {"number": "", "name": ""}
    
    return jsonify({"success": True})

@app.route('/api/command/<command>', methods=['POST'])
def send_command(command):
    global commands_sent, error_count, total_latency_ms
    if not current_instance or 'client' not in current_instance:
        return jsonify({"success": False, "error": "Not connected to any QLab instance"})
    client = current_instance["client"]
    start_time = time.time()
    try:
        if command == "play":
            success = client.play()
        elif command == "stop":
            success = client.stop()
        elif command == "next":
            success = client.next()
        elif command == "previous":
            success = client.previous()
        elif command == "panic":
            success = client.panic()
        elif command == "reset":
            success = client.reset()
        else:
            success = False
            error_msg = f"Unknown command: {command}"
    except Exception as e:
        success = False
        error_msg = str(e)
        logger.error(f"Error sending command {command}: {e}")
    latency_ms = (time.time() - start_time) * 1000

    # update performance counters
    commands_sent += 1
    total_latency_ms += latency_ms
    if not success:
        error_count += 1

    return jsonify({
        "success": success,
        "latency_ms": latency_ms,
        "error": error_msg if not success else None
    })

@app.route('/api/cue_info')
def get_cue_info():
    """Return the last-cached current and next cue (no new AppleScript call)."""
    if not current_instance or 'client' not in current_instance:
        return jsonify({"success": False, "error": "Not connected to any QLab instance"})
    client = current_instance["client"]
    return jsonify({
        "success": True,
        "current": {
            "number": client.current_selected_cue.get("number", "N/A"),
            "name":   client.current_selected_cue.get("name",   "Unnamed"),
            "type":   client.current_selected_cue.get("type",   "Unknown")
        },
        "next": {
            "number": client.next_cue.get("number", "N/A"),
            "name":   client.next_cue.get("name",   "Unnamed"),
            "type":   client.next_cue.get("type",   "Unknown")
        }
    })

@app.route('/api/performance')
def get_performance():
    """Return live performance statistics."""
    avg = (total_latency_ms / commands_sent) if commands_sent else 0.0
    err_rate = (error_count / commands_sent * 100.0) if commands_sent else 0.0
    return jsonify({
        "connected": current_instance is not None,
        "average_latency": round(avg, 1),
        "commands_sent": commands_sent,
        "error_rate": round(err_rate, 1)
    })

@app.route('/api/skip', methods=['POST'])
def skip_to_cue():
    """Skip to specified cue by uniqueID (select it without playing)"""
    if not current_instance or 'client' not in current_instance:
        return jsonify({"success": False, "error": "Not connected"})
    data = request.get_json() or {}
    cue_id = data.get("cue")
    if not cue_id:
        return jsonify({"success": False, "error": "No cue ID provided"})
    
    client = current_instance["client"]
    start_time = time.time()
    
    try:
        # Use the correct AppleScript syntax to select a cue - we need to set the 'selected' property
        # of the workspace to a list containing our cue
        script = f'''
        tell application id "{client.client.bundle_id}"
            try
                set ws to front workspace
                set foundCue to first cue of ws whose uniqueID is "{cue_id}"
                if foundCue exists then
                    -- Set the selected property to our cue
                    set selected of ws to {{foundCue}}
                    return "ok"
                else
                    return "Error: Cue not found"
                end if
            on error errMessage
                return "Error: " & errMessage
            end try
        end tell
        '''
        logger.info(f"Selecting cue with ID: {cue_id}")
        result, err = client.client.run_apple_script(script)
        
        if err:
            logger.error(f"Error in skip_to_cue: {err}")
            return jsonify({"success": False, "error": str(err)})
        
        # Check if the result contains an error message
        if result and result.stringValue().startswith("Error:"):
            error_msg = result.stringValue()
            logger.error(f"Error from AppleScript: {error_msg}")
            return jsonify({"success": False, "error": error_msg})
        
        logger.info("Successfully selected cue")
        success = True
    except Exception as e:
        logger.error(f"Exception in skip_to_cue endpoint: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)})
    
    latency_ms = (time.time() - start_time) * 1000
    
    return jsonify({
        "success": success, 
        "latency_ms": latency_ms
    })

@app.route('/api/cues')
def list_cues():
    """Return list of cues for the active workspace"""
    if not current_instance or 'client' not in current_instance:
        return jsonify({"cues": []})
    cues = current_instance['client'].get_all_cues()
    return jsonify({"cues": cues})

def discover_qlab_instances():
    """Start discovering QLab instances and available workspaces"""
    try:
        # Check if QLab is running using ScriptingBridge
        qlab_app = SBApplication.applicationWithBundleIdentifier_("com.figure53.QLab.4")
        
        if qlab_app.isRunning():
            # QLab is running, add it to our discovered instances
            global discovered_instances
            
            # Create a client to get workspace information
            client = QLabAppleScriptClient()
            workspaces = client.get_workspaces()
            
            if not workspaces:
                # fallback: grab the front workspace’s actual ID and name
                # get workspace ID
                res_id, _ = client.run_apple_script(f'''
                    tell application id "{client.bundle_id}"
                        id of front workspace
                    end tell
                ''')
                default_id = res_id.stringValue() if res_id and res_id.stringValue() else ""
                # get workspace Name
                res_nm, _ = client.run_apple_script(f'''
                    tell application id "{client.bundle_id}"
                        name of front workspace
                    end tell
                ''')
                default_name = res_nm.stringValue() if res_nm and res_nm.stringValue() else "Default Workspace"
                instance = {
                    "name": default_name,
                    "workspace_id": default_id,
                    "ip": "localhost",
                    "port": 0,
                    "hostname": "localhost",
                    "id": 0
                }
                discovered_instances = [instance]
                logger.info("QLab is running but no workspaces found, using default")
            else:
                # Create an instance entry for each workspace
                discovered_instances = []
                for i, workspace in enumerate(workspaces):
                    instance = {
                        "name": f"QLab - {workspace['name']}",
                        "ip": "localhost",
                        "port": 0,
                        "hostname": "localhost",
                        "id": i,
                        "workspace_id": workspace['id']
                    }
                    discovered_instances.append(instance)
                
                logger.info(f"Found {len(workspaces)} QLab workspace(s)")
            
            return discovered_instances, None
        else:
            logger.warning("QLab is not running")
            discovered_instances = []
            return None, "QLab is not running"
    except Exception as e:
        logger.error(f"Failed to detect QLab via AppleScript: {e}")
        discovered_instances = []
        return None, f"Error: {e}"

def periodic_update(interval=0.1):
    """Background thread to refresh current/next cue at a fixed interval."""
    while True:
        if current_instance and 'client' in current_instance:
            try:
                current_instance['client'].update_cue_info()
            except Exception:
                pass
        time.sleep(interval)

if __name__ == "__main__":
    logger.info("Starting qLab Remote control application")
    logger.info(f"Web interface will be available at http://localhost:{WEB_PORT}")
    
    # Get the local IP address to show a more useful URL
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        logger.info(f"Local network access: http://{local_ip}:{WEB_PORT}")
    except Exception as e:
        logger.warning(f"Could not determine local IP: {e}")
    
    # Start with an AppleScript-based discovery instead of ZeroConf
    discover_qlab_instances()
    
    # Open browser window
    try:
        if platform.system() == "Darwin":
            webbrowser.open(f"http://localhost:{WEB_PORT}")
    except Exception as e:
        logger.error(f"Could not open web browser: {e}")
        logger.info(f"Please manually navigate to http://localhost:{WEB_PORT}")
    
    # Start periodic updater so HTTP endpoints use cached cues
    updater = threading.Thread(target=periodic_update, args=(0.1,), daemon=True)
    updater.start()
    
    try:
        # Start the web server
        socketio.run(app, host='0.0.0.0', port=WEB_PORT, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
    finally:
        logger.info("Cleanup complete")
