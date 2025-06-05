from fastapi import FastAPI, Request
import websockets
import asyncio
import json
import uvicorn

app = FastAPI()

@app.post("/ws_bridge")
async def websocket_bridge(request: Request):
    data = await request.json()
    ws_url = data.get("ws_url")
    token = data.get("token")
    method = data.get("method")
    args = data.get("args", {})

    if not all([ws_url, token, method]):
        return {"error": "Missing one of: ws_url, token, method"}

    try:
        async with websockets.connect(ws_url) as ws:
            await ws.recv()  # hello

            await ws.send(json.dumps({
                "type": "auth",
                "access_token": token
            }))
            await ws.recv()  # auth_ok

            # Kirim method & args ke websocket
            command = {
                "id": 1,
                "type": method,
                **args
            }

            await ws.send(json.dumps(command))

            while True:
                message = await ws.recv()
                parsed = json.loads(message)

                # Filter response selain event/ping
                if parsed.get("type") not in ("event", "ping"):
                    return parsed

    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
