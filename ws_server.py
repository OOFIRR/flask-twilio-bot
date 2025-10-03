import asyncio
import websockets
import json

async def handle_connection(websocket, path):
    print("🔌 WebSocket connection started.")
    try:
        async for message in websocket:
            data = json.loads(message)
            event = data.get("event")

            if event == "start":
                print("📞 Call started")
            elif event == "media":
                payload = data.get("media", {}).get("payload")
                print(f"🎤 Received audio payload (len={len(payload)})")
            elif event == "stop":
                print("🛑 Call ended")
    except websockets.exceptions.ConnectionClosed as e:
        print(f"❌ Connection closed: {e}")

async def main():
    async with websockets.serve(handle_connection, "0.0.0.0", 8080):
        print("🔊 WebSocket server is running on ws://0.0.0.0:8080")
        await asyncio.Future()  # keep alive

if __name__ == "__main__":
    asyncio.run(main())
