import json, random, logging
import websockets
from fastapi import FastAPI, Request
import asyncio

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

app = FastAPI()

@app.post("/ws_bridge")
async def websocket_bridge(request: Request):
    data = await request.json()
    ws_url = data.get("ws_url")
    token = data.get("token")
    method = data.get("method")
    args = data.get("args", {})

    logging.info(f"[REQUEST] method={method}, args={args}, ws_url={ws_url}")

    if not all([ws_url, token, method]):
        logging.error("[ERROR] Missing parameters")
        return {"error": "Missing ws_url, token or method"}

    req_id = random.randint(1, 9999999)

    try:
        async with websockets.connect(ws_url) as ws:
            await ws.recv()  # hello

            await ws.send(json.dumps({
                "type": "auth",
                "access_token": token
            }))
            
            auth_resp = json.loads(await ws.recv())  # auth_ok

            if auth_resp.get("type") != "auth_ok":
                return {"error": "Auth Failed"}

            command = {
                "id": req_id,
                "type": method,
                **args
            }

            logging.info("[SEND CMD] " + json.dumps(command))
            await ws.send(json.dumps(command))

            while True:
                msg = await ws.recv()
                logging.info(f"[RECV AUTH] {msg}")
                parsed = json.loads(msg)

                # ignore unrelated messages
                if parsed.get("id") != req_id:
                    continue

                logging.info(f"[MATCHED RESPONSE] {parsed}")

                # must wait until success response with result payload
                if parsed.get("type") == "result":
                    if parsed.get("success") is True:
                        # result may not always exist
                        return parsed

    except Exception as e:
        logging.exception("[ERROR in websocket_bridge]")
        return {"error": str(e)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
