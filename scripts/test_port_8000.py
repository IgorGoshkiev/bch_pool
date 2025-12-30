import asyncio
import websockets


async def test():
    # Тестируем разные endpoints
    endpoints = [
        "ws://127.0.0.1:8000/ws-test",
        "ws://127.0.0.1:8000/stratum/ws/test_miner"
    ]

    for uri in endpoints:
        print(f"\nТестируем: {uri}")

        try:
            async with websockets.connect(uri) as ws:
                print("Подключились!")

                # Получаем ответ
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    print(f"Ответ: {message}")
                except asyncio.TimeoutError:
                    print("Сервер не ответил")

                return True

        except websockets.exceptions.InvalidStatusCode as e:
            print(f"HTTP ошибка {e.status_code}")
        except Exception as e:
            print(f"Ошибка: {type(e).__name__}: {e}")

    return False


if __name__ == "__main__":
    result = asyncio.run(test())
    if not result:
        print("\nПроблема в основном приложении на порту 8000")