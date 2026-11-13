# Mafia Night

A small, mobile-friendly Mafia game lobby built with FastAPI, vanilla HTML/CSS/JavaScript, and WebSockets.

## Features

- Create a room and share its six-character code.
- Join from multiple phones using a name and room code.
- The organizer is also a player and receives a random role.
- Exactly four mandatory roles: Game Master, Mafia, Protector, Detective.
- All remaining players receive numbered Civilian roles.
- Role assignments are made on the server and are never included in the shared lobby broadcast.
- Private reveal/hide controls.
- Organizer-only start and reset controls.
- Reconnecting browsers recover their own role while the server process remains alive.
- No database required for the initial version.

## Run locally

Requires Python 3.10+.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://127.0.0.1:8000 on each device. For phones on the same Wi-Fi, use your computer's LAN IP address and port 8000 (allow the port through your firewall).

## Deploy to Render

1. Create a GitHub repository and push these files to its root.
2. In Render, choose **New + → Blueprint** and connect the repository.
3. Render detects `render.yaml` and creates the web service.
4. Open the generated `onrender.com` URL and share it with players.

You can also create a Render **Web Service** manually with:
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

## Important free-tier limitations

This MVP stores rooms in server memory. Render free services can spin down when idle, and a restart/deploy clears active rooms. Create a new room after a restart. Do not use this in multiple server instances without moving room state and WebSocket coordination to shared infrastructure (for example Redis).

The app is intended for casual private play, not high-stakes security. Anyone with a player's browser session can see that player's role. Players should keep their own screens private.
