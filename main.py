import asyncio
import random
import re
import secrets
import string
from contextlib import asynccontextmanager
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

MANDATORY_ROLES = ["Game Master", "Mafia", "Protector", "Detective"]
ROOM_CODE_ALPHABET = string.ascii_uppercase + string.digits


class Player:
    def __init__(self, player_id: str, name: str, is_organizer: bool = False):
        self.id = player_id
        self.name = name
        self.is_organizer = is_organizer
        self.connected = False
        self.role: Optional[str] = None
        self.websocket: Optional[WebSocket] = None


class Room:
    def __init__(self, code: str):
        self.code = code
        self.players: Dict[str, Player] = {}
        self.status = "lobby"
        self.lock = asyncio.Lock()


rooms: Dict[str, Room] = {}


class CreateRoomRequest(BaseModel):
    name: str = Field(min_length=1, max_length=24)


class JoinRoomRequest(BaseModel):
    code: str = Field(min_length=4, max_length=8)
    name: str = Field(min_length=1, max_length=24)


def clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        raise HTTPException(status_code=422, detail="Please enter your name.")
    return name[:24]


def new_room_code() -> str:
    while True:
        code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(6))
        if code not in rooms:
            return code


def public_state(room: Room, viewer_id: Optional[str] = None) -> dict:
    viewer = room.players.get(viewer_id) if viewer_id else None
    return {
        "code": room.code,
        "status": room.status,
        "players": [
            {
                "id": p.id,
                "name": p.name,
                "is_organizer": p.is_organizer,
                "connected": p.connected,
                "has_role": p.role is not None,
            }
            for p in room.players.values()
        ],
        "you": (
            {
                "id": viewer.id,
                "is_organizer": viewer.is_organizer,
                "role": viewer.role if room.status == "started" else None,
            }
            if viewer
            else None
        ),
    }


async def send_state_to_player(room: Room, player: Player):
    if not player.websocket:
        return
    try:
        await player.websocket.send_json({"type": "state", "data": public_state(room, player.id)})
    except Exception:
        pass


async def broadcast_state(room: Room):
    # Each player receives a separately constructed payload. No role map is broadcast.
    await asyncio.gather(
        *(send_state_to_player(room, p) for p in list(room.players.values()) if p.websocket),
        return_exceptions=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    rooms.clear()


app = FastAPI(title="Mafia Night", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def home():
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/api/rooms")
async def create_room(payload: CreateRoomRequest):
    name = clean_name(payload.name)
    code = new_room_code()
    room = Room(code)
    player_id = secrets.token_urlsafe(24)
    player = Player(player_id, name, is_organizer=True)
    room.players[player_id] = player
    rooms[code] = room
    return {
        "code": code,
        "player_id": player_id,
        "name": name,
        "is_organizer": True,
        "state": public_state(room, player_id),
    }


@app.post("/api/rooms/{code}/join")
async def join_room(code: str, payload: JoinRoomRequest):
    code = code.strip().upper()
    name = clean_name(payload.name)
    room = rooms.get(code)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found. Check the room code.")
    async with room.lock:
        if room.status != "lobby":
            raise HTTPException(status_code=409, detail="This game has already started.")
        if len(room.players) >= 30:
            raise HTTPException(status_code=409, detail="This room is full (maximum 30 players).")
        if any(p.name.casefold() == name.casefold() for p in room.players.values()):
            raise HTTPException(status_code=409, detail="That name is already in this room. Try another name.")
        player_id = secrets.token_urlsafe(24)
        player = Player(player_id, name)
        room.players[player_id] = player
        result = {
            "code": code,
            "player_id": player_id,
            "name": name,
            "is_organizer": False,
            "state": public_state(room, player_id),
        }
    await broadcast_state(room)
    return result


@app.post("/api/rooms/{code}/start")
async def start_game(code: str, payload: dict):
    code = code.strip().upper()
    room = rooms.get(code)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found.")
    player_id = str(payload.get("player_id", ""))
    async with room.lock:
        organizer = room.players.get(player_id)
        if not organizer or not organizer.is_organizer:
            raise HTTPException(status_code=403, detail="Only the organizer can start the game.")
        if room.status != "lobby":
            raise HTTPException(status_code=409, detail="The game has already started.")
        players = list(room.players.values())
        if len(players) < 4:
            raise HTTPException(status_code=400, detail="You need at least 4 players to start.")
        roles = MANDATORY_ROLES + [f"Civilian {i}" for i in range(1, len(players) - 3)]
        random.SystemRandom().shuffle(roles)
        for player, role in zip(players, roles):
            player.role = role
        room.status = "started"
    await broadcast_state(room)
    return {"ok": True}


@app.post("/api/rooms/{code}/reset")
async def reset_game(code: str, payload: dict):
    code = code.strip().upper()
    room = rooms.get(code)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found.")
    player_id = str(payload.get("player_id", ""))
    async with room.lock:
        organizer = room.players.get(player_id)
        if not organizer or not organizer.is_organizer:
            raise HTTPException(status_code=403, detail="Only the organizer can reset the game.")
        room.status = "lobby"
        for player in room.players.values():
            player.role = None
    await broadcast_state(room)
    return {"ok": True}


@app.websocket("/ws/{code}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, code: str, player_id: str):
    code = code.strip().upper()
    room = rooms.get(code)
    if not room or player_id not in room.players:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    player = room.players[player_id]
    old_socket = player.websocket
    player.websocket = websocket
    player.connected = True
    if old_socket and old_socket is not websocket:
        try:
            await old_socket.close(code=1000)
        except Exception:
            pass

    await send_state_to_player(room, player)
    await broadcast_state(room)

    try:
        while True:
            # Client sends lightweight pings to keep the connection alive.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if player.websocket is websocket:
            player.websocket = None
            player.connected = False
            await broadcast_state(room)
