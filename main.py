import kasa
import requests
import time
import asyncio
import threading
import os
import queue

from dotenv import load_dotenv
from kasa import Discover

load_dotenv()
from flask import Flask, render_template, jsonify, request

# API ROUTES
TWITCH_TOKEN_ROUTE = "https://id.twitch.tv/oauth2/token"
TWITCH_USERS_ROUTE = "https://api.twitch.tv/helix/users"
TWITCH_STREAMS_ROUTE = "https://api.twitch.tv/helix/streams"

# Runtime configuration is loaded from .env (never commit that file).
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "henyathegenius")
KASA_OUTLET_IP = os.environ["KASA_OUTLET_IP"]
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", "5000"))

app = Flask(__name__)

# Shared state
mode = "auto"  # "auto", "manual-on", "manual-off"
outlet_online = False
twitch_live = False
status_message = "Starting..."

# Command queue: web handler puts commands here, light thread picks them up instantly
cmd_queue = queue.Queue()

# Kasa device - only touched by light_thread
kasa_outlet = None


def get_access_token():
    params = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
    }
    response = requests.post(TWITCH_TOKEN_ROUTE, params=params)
    return response.json().get("access_token")


def get_channel_id(access_token, channel_name):
    headers = {"Client-ID": CLIENT_ID, "Authorization": f"Bearer {access_token}"}
    params = {"login": channel_name}
    response = requests.get(TWITCH_USERS_ROUTE, headers=headers, params=params)
    data = response.json()
    return data["data"][0]["id"] if data["data"] else None


def is_channel_live(access_token, user_id):
    url = f"{TWITCH_STREAMS_ROUTE}?user_id={user_id}"
    headers = {"Client-ID": CLIENT_ID, "Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)
    data = response.json()
    return bool(data["data"])


# ---------- Kasa helper (must run in its own async context) ----------

kasa_loop = asyncio.new_event_loop()

async def _kasa_connect():
    global kasa_outlet
    outlet = await Discover.discover_single(KASA_OUTLET_IP)
    if isinstance(outlet, kasa.Device):
        kasa_outlet = outlet
        await outlet.update()
        return True
    return False

async def _kasa_on():
    global kasa_outlet, outlet_online
    if kasa_outlet:
        await kasa_outlet.update()
        if kasa_outlet.is_off:
            await kasa_outlet.turn_on()
            await kasa_outlet.update()
        outlet_online = True
        print("Light turned ON")

async def _kasa_off():
    global kasa_outlet, outlet_online
    if kasa_outlet:
        await kasa_outlet.update()
        if kasa_outlet.is_on:
            await kasa_outlet.turn_off()
            await kasa_outlet.update()
        outlet_online = False
        print("Light turned OFF")


def light_thread():
    """Dedicated thread for ALL Kasa operations.
    
    Runs an asyncio event loop that processes commands from cmd_queue
    immediately (non-blocking get with short timeout).
    Twitch monitoring also lives here on its own timer.
    """
    global status_message, outlet_online, twitch_live, mode

    asyncio.set_event_loop(kasa_loop)

    # Connect to Kasa
    try:
        kasa_loop.run_until_complete(_kasa_connect())
        status_message = f"Connected to Kasa outlet at {KASA_OUTLET_IP}"
        print(status_message)
    except Exception as e:
        status_message = f"Kasa error: {e}"
        print(status_message)
        return

    # Get Twitch credentials
    access_token = get_access_token()
    channel_id = get_channel_id(access_token, CHANNEL_NAME)
    if not channel_id:
        status_message = f'User "{CHANNEL_NAME}" not found!'
        print(status_message)
        return

    status_message = f"Monitoring {CHANNEL_NAME}..."
    print(status_message)

    last_twitch_check = 0
    TWITCH_INTERVAL = 60  # seconds between Twitch checks

    while True:
        # --- 1. Process ALL pending commands immediately ---
        while True:
            try:
                cmd = cmd_queue.get_nowait()
            except queue.Empty:
                break

            current_mode = mode  # snapshot
            if cmd == "on":
                kasa_loop.run_until_complete(_kasa_on())
            elif cmd == "off":
                kasa_loop.run_until_complete(_kasa_off())

        # --- 2. Auto mode: check Twitch on schedule ---
        current_mode = mode
        now = time.time()
        if current_mode == "auto" and (now - last_twitch_check) >= TWITCH_INTERVAL:
            last_twitch_check = now
            try:
                if access_token is None:
                    access_token = get_access_token()
                    channel_id = get_channel_id(access_token, CHANNEL_NAME)

                live = is_channel_live(access_token, channel_id)
                twitch_live = live

                if live:
                    print(f"{CHANNEL_NAME} is LIVE!")
                    kasa_loop.run_until_complete(_kasa_on())
                else:
                    print(f"{CHANNEL_NAME} is OFFLINE.")
                    kasa_loop.run_until_complete(_kasa_off())
            except Exception as e:
                print(f"Error checking Twitch: {e}")
                status_message = f"Error: {e}"
                access_token = None
        elif current_mode != "auto":
            # Still update twitch status for the UI
            try:
                if access_token is None:
                    access_token = get_access_token()
                    channel_id = get_channel_id(access_token, CHANNEL_NAME)
                twitch_live = is_channel_live(access_token, channel_id)
            except Exception:
                pass

        # --- 3. Sleep briefly and loop ---
        time.sleep(0.5)


# ---- Web Routes ----

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    return jsonify({
        "mode": mode,
        "twitch_live": twitch_live,
        "outlet_on": outlet_online,
        "status_message": status_message,
        "channel_name": CHANNEL_NAME,
        "outlet_ip": KASA_OUTLET_IP,
    })


@app.route("/api/mode", methods=["POST"])
def api_set_mode():
    global mode
    data = request.get_json()
    new_mode = data.get("mode")
    if new_mode not in ("auto", "manual-on", "manual-off"):
        return jsonify({"error": "Invalid mode"}), 400

    old_mode = mode
    mode = new_mode
    print(f"Mode changed: {old_mode} -> {new_mode}")

    # Queue command for instant processing by light_thread
    if new_mode == "manual-on":
        cmd_queue.put("on")
    elif new_mode == "manual-off":
        cmd_queue.put("off")
    # auto mode: the light_thread's Twitch check will handle it on next tick

    return jsonify({"mode": mode, "outlet_on": outlet_online})


if __name__ == "__main__":
    # Start the light/control thread
    lt = threading.Thread(target=light_thread, daemon=True)
    lt.start()

    # Start Flask bound to Tailscale IP only
    print(f"Starting web interface on {WEB_HOST}:{WEB_PORT}")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False)
