# DayoLightController

A small Flask web interface that controls a Kasa smart outlet based on whether a Twitch channel is live. It can also be switched between automatic, manual-on, and manual-off modes from the web UI.

## How it works

1. The controller connects to the configured Kasa outlet using `python-kasa`.
2. It checks the configured Twitch channel periodically through the Twitch Helix API.
3. In automatic mode, the outlet turns on while the channel is live and turns off when it is offline.
4. The Flask interface exposes the current state and manual mode controls.

## Requirements

- Docker Engine and Docker Compose
- Network access from the host/container to the Kasa outlet
- A Twitch application with a Client ID and Client Secret
- The Kasa outlet's current IP address
- A host/network route for the web interface, if it should be accessed remotely
- Optional: an HTTPS reverse proxy and DNS record for public or tailnet access

The Kasa device must be reachable from the machine running the container. Kasa discovery uses the configured IP and communicates with the outlet over its local network protocol; this is not a cloud-only integration.

## Configuration

Copy the example file and fill in the values:

```bash
cp .env.example .env
```

`.env` is intentionally ignored by Git. The variables are:

| Variable | Purpose |
| --- | --- |
| `CLIENT_ID` | Twitch application Client ID |
| `CLIENT_SECRET` | Twitch application Client Secret |
| `CHANNEL_NAME` | Twitch login name to monitor |
| `KASA_OUTLET_IP` | IP address of the Kasa outlet |
| `WEB_HOST` | Flask bind address inside the container |
| `WEB_PORT` | Container/web application port |
| `CONTROLLER_BIND_IP` | Host IP to bind the published Docker port |

Do not commit `.env`, credentials, tokens, or other secrets.

## Deploy with Docker Compose

From the repository directory:

```bash
docker compose up -d --build
```

Check the container and recent startup output:

```bash
docker compose ps
docker compose logs --tail=100 app
```

The API status endpoint is:

```text
http://<controller-host>:5000/api/status
```

A successful startup should report a message similar to `Connected to Kasa outlet at <KASA_OUTLET_IP>` in the logs. If it reports a discovery timeout, verify the outlet IP, VLAN/firewall rules, and that the host has a route to the Kasa device.

To update an existing deployment after source or configuration changes:

```bash
docker compose up -d --build
```

To stop it:

```bash
docker compose down
```

## Reverse proxy and HTTPS

The included Compose file binds the controller to `CONTROLLER_BIND_IP` and `WEB_PORT`. A reverse proxy can forward a hostname to that host and port. For example, a proxy on the same host can forward:

```text
dayolight.example → http://100.126.197.74:5000
```

Create the DNS record and TLS certificate through the reverse proxy's normal workflow. Keep the controller's direct port restricted to the intended network; HTTPS should be terminated at the reverse proxy when the interface is accessed over an untrusted network.

## Development

Install the dependencies into a virtual environment, create `.env`, and run:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python main.py
```

The application listens on `WEB_HOST:WEB_PORT` and serves the UI at `/`.

## API endpoints

- `GET /` — web interface
- `GET /api/status` — current controller, Twitch, and outlet status
- `POST /api/mode` — set `auto`, `manual-on`, or `manual-off`

Example:

```bash
curl -X POST http://localhost:5000/api/mode \
  -H 'Content-Type: application/json' \
  -d '{"mode":"manual-on"}'
```

## Notes

This project currently uses Flask's built-in development server because it is a small home automation controller. If it is exposed beyond a trusted tailnet or LAN, put it behind an authenticated reverse proxy and a production WSGI server before broadening access.
