"""
BlackLine Client
Remote control client with auto-reconnect and command handling.
NEVER crashes - all exceptions are handled gracefully.
"""

import asyncio
import json
import base64
import io
import os
import socket
import webbrowser
from typing import Optional

import websockets
import mss
import mss.tools
import pyautogui

# ==================== CONFIG ====================
SERVER_URL = os.getenv("BLACKLINE_SERVER", "ws://localhost:8765/ws")
RECONNECT_DELAY = 5  # seconds
SCREENSHOT_QUALITY = 85  # JPEG quality (1-100)
STREAM_FPS = 30  # Target FPS for screen streaming


class BlackLineClient:
    """Main client class with all functionality."""
    
    def __init__(self, server_url: str = SERVER_URL):
        self.server_url = server_url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.client_name = self._get_client_name()
        self.is_streaming = False
        self.stream_task: Optional[asyncio.Task] = None
        
    def _get_client_name(self) -> str:
        """Get unique client name based on hostname."""
        try:
            return socket.gethostname()
        except Exception:
            return "unknown-client"
    
    async def connect(self) -> bool:
        """Establish WebSocket connection with retry logic."""
        url = f"{self.server_url}/{self.client_name}"
        
        try:
            self.ws = await websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_size=10 * 1024 * 1024  # 10MB max message size
            )
            print(f"[+] Connected to server as '{self.client_name}'")
            return True
        except Exception as e:
            print(f"[!] Connection failed: {e}")
            return False
    
    async def disconnect(self):
        """Close WebSocket connection gracefully."""
        try:
            self.is_streaming = False
            
            if self.stream_task:
                self.stream_task.cancel()
                try:
                    await self.stream_task
                except asyncio.CancelledError:
                    pass
                self.stream_task = None
            
            if self.ws:
                await self.ws.close()
                self.ws = None
                
            print("[-] Disconnected from server")
        except Exception as e:
            print(f"[!] Error during disconnect: {e}")
    
    async def send(self, data: dict):
        """Send JSON data to server."""
        if self.ws:
            try:
                await self.ws.send(json.dumps(data))
            except Exception as e:
                print(f"[!] Send error: {e}")
    
    async def send_ack(self, command: str):
        """Send acknowledgment to server."""
        await self.send({"type": "ack", "command": command})
    
    def _capture_screen(self) -> bytes:
        """Capture screen and return as JPEG bytes."""
        try:
            with mss.mss() as sct:
                # Capture primary monitor
                monitor = sct.monitors[1]  # Monitor 1 is the primary screen
                screenshot = sct.grab(monitor)
                
                # Convert to JPEG
                img = mss.tools.to_png(screenshot.rgb, screenshot.size)
                
                # Convert PNG to JPEG for smaller size
                from PIL import Image
                with Image.open(io.BytesIO(img)) as pil_img:
                    jpeg_buffer = io.BytesIO()
                    pil_img.convert('RGB').save(
                        jpeg_buffer,
                        format='JPEG',
                        quality=SCREENSHOT_QUALITY,
                        optimize=True
                    )
                    return jpeg_buffer.getvalue()
                    
        except Exception as e:
            print(f"[!] Screenshot error: {e}")
            return b""
    
    async def _stream_screen(self):
        """Continuous screen streaming loop."""
        frame_delay = 1.0 / STREAM_FPS
        
        while self.is_streaming:
            try:
                start_time = asyncio.get_event_loop().time()
                
                # Capture and send frame
                image_data = self._capture_screen()
                if image_data:
                    await self.send({
                        "type": "stream_frame",
                        "image": base64.b64encode(image_data).decode('ascii')
                    })
                
                # Maintain FPS
                elapsed = asyncio.get_event_loop().time() - start_time
                sleep_time = max(0, frame_delay - elapsed)
                await asyncio.sleep(sleep_time)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[!] Stream error: {e}")
                await asyncio.sleep(1)  # Pause on error
    
    # ==================== COMMAND HANDLERS ====================
    
    async def handle_screenshot_request(self):
        """Handle single screenshot request."""
        try:
            image_data = self._capture_screen()
            if image_data:
                await self.send({
                    "type": "screenshot",
                    "image": base64.b64encode(image_data).decode('ascii')
                })
            await self.send_ack("screenshot_request")
        except Exception as e:
            print(f"[!] Screenshot request error: {e}")
    
    async def handle_shutdown(self):
        """Handle shutdown command."""
        try:
            print("[*] Shutdown command received")
            await self.send_ack("shutdown")
            
            # Give time for ack to be sent
            await asyncio.sleep(0.5)
            
            # Perform shutdown based on OS
            if os.name == 'nt':  # Windows
                os.system("shutdown /s /t 1")
            else:  # Linux/Mac
                os.system("shutdown now")
                
        except Exception as e:
            print(f"[!] Shutdown error: {e}")
    
    async def handle_browser(self, url: str):
        """Handle open URL command."""
        try:
            print(f"[*] Opening URL: {url}")
            webbrowser.open(url)
            await self.send_ack("browser")
        except Exception as e:
            print(f"[!] Browser error: {e}")
    
    async def handle_type(self, text: str):
        """Handle type text command."""
        try:
            print(f"[*] Typing text: {text[:50]}..." if len(text) > 50 else f"[*] Typing text: {text}")
            pyautogui.write(text, interval=0.05)
            await self.send_ack("type")
        except Exception as e:
            print(f"[!] Type error: {e}")
    
    async def handle_mouse_event(self, event: str, **kwargs):
        """Handle mouse events."""
        try:
            x = kwargs.get('x')
            y = kwargs.get('y')
            button = kwargs.get('button', 1)
            delta_x = kwargs.get('deltaX', 0)
            delta_y = kwargs.get('deltaY', 0)
            
            # Map button: 0=left, 1=middle, 2=right
            button_map = {0: 'left', 1: 'middle', 2: 'right'}
            pyautogui_button = button_map.get(button, 'left')
            
            if event == 'mousemove':
                if x is not None and y is not None:
                    pyautogui.moveTo(x, y, duration=0.1)
                    
            elif event == 'mousedown':
                if x is not None and y is not None:
                    pyautogui.moveTo(x, y, duration=0.1)
                pyautogui.mouseDown(button=pyautogui_button)
                
            elif event == 'mouseup':
                pyautogui.mouseUp(button=pyautogui_button)
                
            elif event == 'click':
                if x is not None and y is not None:
                    pyautogui.moveTo(x, y, duration=0.1)
                pyautogui.click(button=pyautogui_button)
                
            elif event == 'doubleclick':
                if x is not None and y is not None:
                    pyautogui.moveTo(x, y, duration=0.1)
                pyautogui.doubleClick(button=pyautogui_button)
                
            elif event == 'wheel':
                pyautogui.scroll(int(-delta_y))
                
            await self.send_ack("mouse_event")
            
        except Exception as e:
            print(f"[!] Mouse event error: {e}")
    
    async def handle_keyboard_event(self, event: str, key: str = None, code: str = None):
        """Handle keyboard events."""
        try:
            if event == 'keydown':
                # Handle special keys
                key_map = {
                    'Enter': 'enter',
                    'Escape': 'esc',
                    'Backspace': 'backspace',
                    'Delete': 'delete',
                    'ArrowUp': 'up',
                    'ArrowDown': 'down',
                    'ArrowLeft': 'left',
                    'ArrowRight': 'right',
                    'Tab': 'tab',
                    'Space': 'space',
                    'Control': 'ctrl',
                    'Alt': 'alt',
                    'Shift': 'shift',
                    'Meta': 'command',
                    'F1': 'f1', 'F2': 'f2', 'F3': 'f3', 'F4': 'f4',
                    'F5': 'f5', 'F6': 'f6', 'F7': 'f7', 'F8': 'f8',
                    'F9': 'f9', 'F10': 'f10', 'F11': 'f11', 'F12': 'f12',
                }
                
                pyautogui_key = key_map.get(key, key)
                if pyautogui_key and len(pyautogui_key) == 1:
                    pyautogui.keyDown(pyautogui_key)
                else:
                    pyautogui.keyDown(pyautogui_key)
                    
            elif event == 'keyup':
                key_map = {
                    'Enter': 'enter',
                    'Escape': 'esc',
                    'Backspace': 'backspace',
                    'Delete': 'delete',
                    'ArrowUp': 'up',
                    'ArrowDown': 'down',
                    'ArrowLeft': 'left',
                    'ArrowRight': 'right',
                    'Tab': 'tab',
                    'Space': 'space',
                    'Control': 'ctrl',
                    'Alt': 'alt',
                    'Shift': 'shift',
                    'Meta': 'command',
                }
                
                pyautogui_key = key_map.get(key, key)
                pyautogui.keyUp(pyautogui_key)
                
            await self.send_ack("keyboard_event")
            
        except Exception as e:
            print(f"[!] Keyboard event error: {e}")
    
    async def handle_start_stream(self):
        """Start screen streaming."""
        try:
            if not self.is_streaming:
                self.is_streaming = True
                self.stream_task = asyncio.create_task(self._stream_screen())
                print("[*] Screen streaming started")
            await self.send_ack("start_stream")
        except Exception as e:
            print(f"[!] Start stream error: {e}")
    
    async def handle_stop_stream(self):
        """Stop screen streaming."""
        try:
            self.is_streaming = False
            if self.stream_task:
                self.stream_task.cancel()
                try:
                    await self.stream_task
                except asyncio.CancelledError:
                    pass
                self.stream_task = None
            print("[*] Screen streaming stopped")
            await self.send_ack("stop_stream")
        except Exception as e:
            print(f"[!] Stop stream error: {e}")
    
    # ==================== MESSAGE PROCESSING ====================
    
    async def process_message(self, message: dict):
        """Process incoming message and execute command."""
        try:
            msg_type = message.get('type')
            
            if msg_type is None:
                return
            
            print(f"[*] Received command: {msg_type}")
            
            if msg_type == 'screenshot_request':
                await self.handle_screenshot_request()
                
            elif msg_type == 'shutdown':
                await self.handle_shutdown()
                
            elif msg_type == 'browser':
                url = message.get('url', '')
                await self.handle_browser(url)
                
            elif msg_type == 'type':
                text = message.get('text', '')
                await self.handle_type(text)
                
            elif msg_type == 'mouse_event':
                event = message.get('event', '')
                await self.handle_mouse_event(event, **message)
                
            elif msg_type == 'keyboard_event':
                event = message.get('event', '')
                key = message.get('key')
                code = message.get('code')
                await self.handle_keyboard_event(event, key=key, code=code)
                
            elif msg_type == 'start_stream':
                await self.handle_start_stream()
                
            elif msg_type == 'stop_stream':
                await self.handle_stop_stream()
                
            else:
                print(f"[!] Unknown command: {msg_type}")
                
        except Exception as e:
            print(f"[!] Message processing error: {e}")
    
    # ==================== MAIN LOOP ====================
    
    async def run(self):
        """Main client loop with auto-reconnect."""
        print(f"[*] BlackLine Client starting...")
        print(f"[*] Client name: {self.client_name}")
        print(f"[*] Server: {self.server_url}")
        
        while True:
            try:
                # Connect to server
                if not await self.connect():
                    print(f"[!] Reconnecting in {RECONNECT_DELAY} seconds...")
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue
                
                # Main message loop
                while True:
                    try:
                        message = await self.ws.recv()
                        data = json.loads(message)
                        await self.process_message(data)
                    except websockets.ConnectionClosed:
                        print("[!] Connection closed by server")
                        break
                    except json.JSONDecodeError as e:
                        print(f"[!] Invalid JSON received: {e}")
                        continue
                    except Exception as e:
                        print(f"[!] Receive error: {e}")
                        break
                
            except Exception as e:
                print(f"[!] Critical error: {e}")
            
            # Cleanup and reconnect
            await self.disconnect()
            print(f"[!] Reconnecting in {RECONNECT_DELAY} seconds...")
            await asyncio.sleep(RECONNECT_DELAY)


# ==================== ENTRY POINT ====================

async def main():
    """Entry point with exception handling."""
    client = BlackLineClient()
    
    try:
        await client.run()
    except KeyboardInterrupt:
        print("\n[*] Client stopped by user")
    except Exception as e:
        print(f"[!] Fatal error: {e}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
