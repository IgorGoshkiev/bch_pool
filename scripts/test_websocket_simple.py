from fastapi import FastAPI, WebSocket
import uvicorn

app = FastAPI()


@app.websocket("/test")
async def simple_websocket(websocket: WebSocket):
    # Принимаем ВСЕ соединения без проверок
    await websocket.accept()
    print("Клиент подключился!")

    try:
        # Отправляем приветствие
        await websocket.send_text("Hello from WebSocket!")

        # Ждём сообщений
        while True:
            data = await websocket.receive_text()
            print(f"Получено: {data}")
            await websocket.send_text(f"Echo: {data}")

    except Exception as e:
        print(f"Клиент отключился: {e}")


if __name__ == "__main__":
    print("Запускаем простой WebSocket сервер на http://127.0.0.1:9000")
    print("WebSocket endpoint: ws://127.0.0.1:9000/test")
    uvicorn.run(app, host="127.0.0.1", port=9000)