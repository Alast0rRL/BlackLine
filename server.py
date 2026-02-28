"""
BlackLine Server
FastAPI + WebSocket + Telegram Bot (aiogram)
"""

import asyncio
import json
import base64
import os
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

import database as db

# ==================== CONFIG ====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
WS_HOST = "0.0.0.0"
WS_PORT = 8765
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8000

# ==================== CONNECTION MANAGER ====================
class ConnectionManager:
    """Manages active WebSocket connections to clients."""
    
    def __init__(self):
        # client_id -> WebSocket
        self.active_connections: dict[int, WebSocket] = {}
        # client_name -> client_id mapping
        self.name_to_id: dict[str, int] = {}
    
    async def connect(self, websocket: WebSocket, client_name: str) -> int:
        """Accept connection and register client."""
        await websocket.accept()
        
        # Get or create client in database
        client = await db.get_or_create_client(client_name)
        
        # Store connection
        self.active_connections[client.id] = websocket
        self.name_to_id[client_name] = client.id
        
        # Update status in database
        await db.update_client_status(client.id, db.ClientStatus.ONLINE)
        
        print(f"[+] Client connected: {client_name} (ID: {client.id})")
        return client.id
    
    def disconnect(self, client_id: int):
        """Remove client from active connections."""
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        
        # Remove from name_to_id mapping
        for name, cid in list(self.name_to_id.items()):
            if cid == client_id:
                del self.name_to_id[name]
        
        print(f"[-] Client disconnected: ID {client_id}")
    
    async def send_to_client(self, client_id: int, data: dict) -> bool:
        """Send data to specific client."""
        if client_id not in self.active_connections:
            return False
        
        try:
            ws = self.active_connections[client_id]
            await ws.send_json(data)
            return True
        except Exception as e:
            print(f"[!] Error sending to client {client_id}: {e}")
            return False
    
    async def broadcast(self, data: dict):
        """Broadcast data to all connected clients."""
        for client_id in list(self.active_connections.keys()):
            await self.send_to_client(client_id, data)
    
    def get_online_clients(self) -> list[dict]:
        """Get list of online clients."""
        return [
            {"id": cid, "name": name}
            for name, cid in self.name_to_id.items()
            if cid in self.active_connections
        ]


manager = ConnectionManager()

# ==================== FASTAPI APP ====================
app = FastAPI(title="BlackLine Remote Control")

# Store for pending screenshot requests
screenshot_pending: dict[int, asyncio.Future] = {}


@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    await db.init_db()
    print("[*] Database initialized")
    print(f"[*] WebSocket server: ws://{WS_HOST}:{WS_PORT}")
    print(f"[*] HTTP server: http://{HTTP_HOST}:{HTTP_PORT}")


@app.get("/")
async def get_index():
    """Serve main HTML page."""
    return FileResponse("index.html")


@app.get("/clients")
async def get_clients():
    """Get all clients (online and offline)."""
    clients = await db.get_all_clients()
    return [
        {
            "id": c.id,
            "name": c.name,
            "status": c.status.value,
            "last_seen": c.last_seen
        }
        for c in clients
    ]


@app.websocket("/ws/{client_name}")
async def websocket_endpoint(websocket: WebSocket, client_name: str):
    """WebSocket endpoint for client connections."""
    client_id = await manager.connect(websocket, client_name)
    
    try:
        while True:
            # Receive data from client
            data = await websocket.receive_text()
            
            try:
                message = json.loads(data)
                msg_type = message.get("type")
                
                # Handle screenshot response
                if msg_type == "screenshot" and "image" in message:
                    if client_id in screenshot_pending:
                        screenshot_pending[client_id].set_result(message["image"])
                
                # Handle stream frame (for web interface)
                elif msg_type == "stream_frame" and "image" in message:
                    # Forward to web interface clients (if any)
                    pass  # Can be extended for web-based viewing
                
                # Handle command acknowledgment
                elif msg_type == "ack":
                    print(f"[*] Client {client_id} acknowledged: {message.get('command')}")
                
            except json.JSONDecodeError:
                print(f"[!] Invalid JSON from client {client_id}")
                
    except WebSocketDisconnect:
        manager.disconnect(client_id)
        await db.update_client_status(client_id, db.ClientStatus.OFFLINE)
    except Exception as e:
        print(f"[!] Error in websocket {client_id}: {e}")
        manager.disconnect(client_id)
        await db.update_client_status(client_id, db.ClientStatus.OFFLINE)


# ==================== TELEGRAM BOT ====================
bot: Optional[Bot] = None
dp: Optional[Dispatcher] = None


async def send_screenshot(client_id: int, chat_id: int) -> bool:
    """Request screenshot from client and send to Telegram."""
    if client_id not in manager.active_connections:
        await bot.send_message(chat_id, f"❌ Client {client_id} is offline")
        return False
    
    # Create future for screenshot result
    future = asyncio.Future()
    screenshot_pending[client_id] = future
    
    try:
        # Request screenshot
        await manager.send_to_client(client_id, {"type": "screenshot_request"})
        
        # Wait for response (timeout 10 seconds)
        image_data = await asyncio.wait_for(future, timeout=10.0)
        
        # Decode and send image
        image_bytes = base64.b64decode(image_data)
        await bot.send_photo(chat_id, image_bytes, caption=f"📸 Screenshot from client {client_id}")
        return True
        
    except asyncio.TimeoutError:
        await bot.send_message(chat_id, f"⏱️ Timeout waiting for screenshot from client {client_id}")
        return False
    except Exception as e:
        await bot.send_message(chat_id, f"❌ Error: {e}")
        return False
    finally:
        if client_id in screenshot_pending:
            del screenshot_pending[client_id]


@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    """Show all connected clients."""
    clients = await db.get_all_clients()
    
    if not clients:
        await message.answer("📭 No clients registered yet")
        return
    
    response = "🖥️ *Registered Clients:*\n\n"
    for c in clients:
        status_icon = "🟢" if c.status == db.ClientStatus.ONLINE else "🔴"
        response += f"{status_icon} `{c.id}` - {c.name} ({c.status.value})\n"
    
    await message.answer(response, parse_mode="Markdown")


@dp.message(Command("screenshot"))
async def cmd_screenshot(message: types.Message):
    """Request screenshot from client."""
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Usage: /screenshot <client_id>")
        return
    
    try:
        client_id = int(args[1])
    except ValueError:
        await message.answer("❌ Invalid client ID")
        return
    
    await send_screenshot(client_id, message.chat.id)


@dp.message(Command("shutdown"))
async def cmd_shutdown(message: types.Message):
    """Shutdown client PC."""
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Usage: /shutdown <client_id>")
        return
    
    try:
        client_id = int(args[1])
    except ValueError:
        await message.answer("❌ Invalid client ID")
        return
    
    if client_id not in manager.active_connections:
        await message.answer(f"❌ Client {client_id} is offline")
        return
    
    await manager.send_to_client(client_id, {"type": "shutdown"})
    await message.answer(f"🔌 Shutdown command sent to client {client_id}")


@dp.message(Command("browser"))
async def cmd_browser(message: types.Message):
    """Open URL in browser on client."""
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ Usage: /browser <client_id> <url>")
        return
    
    try:
        client_id = int(args[1])
    except ValueError:
        await message.answer("❌ Invalid client ID")
        return
    
    url = args[2]
    
    if client_id not in manager.active_connections:
        await message.answer(f"❌ Client {client_id} is offline")
        return
    
    await manager.send_to_client(client_id, {"type": "browser", "url": url})
    await message.answer(f"🌐 Opening {url} on client {client_id}")


@dp.message(Command("type"))
async def cmd_type(message: types.Message):
    """Type text on client keyboard."""
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ Usage: /type <client_id> <text>")
        return
    
    try:
        client_id = int(args[1])
    except ValueError:
        await message.answer("❌ Invalid client ID")
        return
    
    text = args[2]
    
    if client_id not in manager.active_connections:
        await message.answer(f"❌ Client {client_id} is offline")
        return
    
    await manager.send_to_client(client_id, {"type": "type", "text": text})
    await message.answer(f"⌨️ Typing text on client {client_id}")


async def start_bot():
    """Start Telegram bot polling."""
    global bot, dp
    
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[!] Telegram bot token not set. Bot will not start.")
        return
    
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    
    # Register handlers
    dp.message(Command("list"))(cmd_list)
    dp.message(Command("screenshot"))(cmd_screenshot)
    dp.message(Command("shutdown"))(cmd_shutdown)
    dp.message(Command("browser"))(cmd_browser)
    dp.message(Command("type"))(cmd_type)
    
    print("[*] Telegram bot started")
    await dp.start_polling(bot)


# ==================== MAIN ====================
async def main():
    """Run FastAPI server and Telegram bot concurrently."""
    import uvicorn
    
    # Create tasks for both servers
    bot_task = asyncio.create_task(start_bot())
    
    # Configure uvicorn
    config = uvicorn.Config(
        app,
        host=HTTP_HOST,
        port=HTTP_PORT,
        log_level="info"
    )
    server = uvicorn.Server(config)
    
    # Run uvicorn server
    await server.serve()
    
    # Cancel bot task when server stops
    bot_task.cancel()
    try:
        await bot_task
    except asyncio.CancelledError:
        pass
    
    # Cleanup
    if bot:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
