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


@app.post("/api/rooms/{code}/close")
async def hard_reset_close_room(code: str, payload: dict):
    code = code.strip().upper()
    room = rooms.get(code)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found. It may already be closed.")
    player_id = str(payload.get("player_id", ""))
    async with room.lock:
        organizer = room.players.get(player_id)
        if not organizer or not organizer.is_organizer:
            raise HTTPException(status_code=403, detail="Only the current organizer can close this room.")
        await close_room(room, "The room was closed by the organizer. Please create or join another game.")
    return {"ok": True, "closed": True}


@app.post("/api/rooms/{code}/leave")
async def leave_room(code: str, payload: dict):
    code = code.strip().upper()
    room = rooms.get(code)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found. It may already be closed.")
    player_id = str(payload.get("player_id", ""))
    async with room.lock:
        player = room.players.get(player_id)
        if not player:
            raise HTTPException(status_code=404, detail="You are no longer in this room.")
        was_organizer = player.is_organizer
        player.is_organizer = False
        room.players.pop(player_id, None)
        player_socket = player.websocket
        player.websocket = None
        if was_organizer and room.players:
            transfer_organizer(room)
        if not room.players:
            rooms.pop(code, None)
        elif was_organizer:
            await broadcast_state(room)
        else:
            await broadcast_state(room)
    if player_socket:
        try:
            await player_socket.close(code=1000, reason="You left the room")
        except Exception:
            pass
    return {"ok": True, "left": True}


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
            # A real disconnect immediately transfers organizer privileges. A
            # refreshed/reconnected browser does not trigger this for its old socket.
            if rooms.get(code) is room and player.is_organizer:
                transfer_organizer(room)
            if rooms.get(code) is room:
                await broadcast_state(room)
