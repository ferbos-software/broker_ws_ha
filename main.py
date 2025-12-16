import json
import logging
import asyncio
from typing import Dict, Optional
from fastapi import FastAPI, Request
import websockets

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)

app = FastAPI()

# Persistent connection pool keyed by ws_url
WS_POOL: Dict[str, "WebsocketClient"] = {}

def short_log(data: dict, limit=300):
    s = json.dumps(data)
    if len(s) > limit:
        return s[:limit] + "... (truncated)"
    return s

# ======================================================
# Persistent Websocket Client
# ======================================================
class WebsocketClient:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self.ws = None
        self.is_connected = False
        self.lock = asyncio.Lock()
        self.last_msg_id = 0

    async def connect(self):
        """Connect once and reuse until error."""
        async with self.lock:
            if self.is_connected and self.ws:
                return  # already online

            logging.info(f"[WS] Connecting to {self.url}...")
            try:
                self.ws = await websockets.connect(
                    self.url,
                    open_timeout=30,
                    ping_timeout=30,
                    close_timeout=10
                )
                self.is_connected = True

                await self._do_handshake()
                logging.info(f"[WS] Connected & authenticated {self.url}")

            except Exception as e:
                self.is_connected = False
                self.ws = None
                logging.error(f"[WS] Connection error: {e}")
                raise e

    async def _do_handshake(self):
        # Receive hello
        hello = await self.ws.recv()
        logging.info(f"[RECV HELLO] {hello}")

        auth_msg = {
            "type": "auth",
            "access_token": self.token
        }
        await self.ws.send(json.dumps(auth_msg))
        logging.info(f"[SEND AUTH] {auth_msg}")

        auth_res = await self.ws.recv()
        logging.info(f"[RECV AUTH OK] {auth_res}")

    async def _wait_for_response(self):
        """Wait for non-event/non-ping response"""
        while True:
            response = await self.ws.recv()
            parsed = json.loads(response)
            
            # We return only the real response (not ping/event)
            if parsed.get("type") not in ("event", "ping"):
                logging.info(f"[RECV RESPONSE] {short_log(parsed)}")
                return parsed

    async def request(self, method: str, args: dict, timeout: float = 8.0) -> dict:
        """Send method call via persistent connection with timeout"""
        await self.connect()

        async with self.lock:
            try:
                self.last_msg_id += 1
                request_obj = { "id": self.last_msg_id, "type": method, **args }

                logging.info(f"[SEND CMD] {request_obj}")
                await self.ws.send(json.dumps(request_obj))

                # Add timeout for receiving response
                try:
                    response = await asyncio.wait_for(
                        self._wait_for_response(),
                        timeout=timeout
                    )
                    return response
                except asyncio.TimeoutError:
                    logging.error(f"[TIMEOUT] No response after {timeout}s for {self.url}")
                    self.is_connected = False
                    self.ws = None
                    raise Exception(f"Request timeout after {timeout}s")

            except Exception as e:
                logging.error(f"[WS ERROR] {e}")
                self.is_connected = False
                self.ws = None
                raise e


# ======================================================
# Helper to get/Create persistent instance
# ======================================================
def get_ws_client(ws_url: str, token: str) -> WebsocketClient:
    if ws_url not in WS_POOL:
        WS_POOL[ws_url] = WebsocketClient(ws_url, token)

    client = WS_POOL[ws_url]
    client.token = token  # update token if renewed

    return client


# ======================================================
# API Endpoint
# ======================================================
@app.post("/ws_bridge")
async def websocket_bridge(request: Request):
    data = await request.json()

    ws_url = data.get("ws_url")
    token = data.get("token")
    method = data.get("method")
    args = data.get("args", {})
    timeout = data.get("timeout", 8.0)  # Default 8 seconds, can be customized

    logging.info(f"[REQUEST] method={method}, timeout={timeout}s")

    if not all([ws_url, token, method]):
        return {"error": "Missing ws_url, token, or method"}

    try:
        client = get_ws_client(ws_url, token)
        result = await client.request(method, args, timeout=timeout)
        return result

    except Exception as e:
        logging.error(f"[FINAL FAILURE ERROR] {str(e)}")
        return {"error": str(e)}