import asyncio
import websockets
import json
import time


async def test_stratum():
    uri = "ws://localhost:8000/stratum/ws/test_miner_address"

    try:
        print(f"Подключаемся к {uri}...")

        # Добавляем заголовки для обхода проверок
        async with websockets.connect(
                uri,
                extra_headers={
                    "Origin": "http://localhost:8000",
                    "User-Agent": "Stratum-Test-Client/1.0"
                }
        ) as websocket:
            print("Подключились к Stratum серверу")

            # 1. Ждём приветственное сообщение
            welcome = await websocket.recv()
            print(f"Приветствие от сервера: {welcome}")

            # 2. Отправляем подписку
            subscribe_msg = {
                "id": 1,
                "method": "mining.subscribe",
                "params": []
            }
            await websocket.send(json.dumps(subscribe_msg))
            response = await websocket.recv()
            print(f"Ответ на подписку: {response}")

            # 3. Авторизация
            auth_msg = {
                "id": 2,
                "method": "mining.authorize",
                "params": ["test_worker", "x"]
            }
            await websocket.send(json.dumps(auth_msg))
            response = await websocket.recv()
            print(f" Ответ на авторизацию: {response}")

            # 4. Ждём задание
            print("Ждём задание от сервера...")
            job = await websocket.recv()
            print(f"Получено задание: {job}")

            # 5. Отправляем тестовый шар
            submit_msg = {
                "id": 3,
                "method": "mining.submit",
                "params": ["test_worker", "job_123", "extra_nonce", "ntime", "nonce"]
            }
            await websocket.send(json.dumps(submit_msg))
            response = await websocket.recv()
            print(f"Ответ на шар: {response}")

            # Ждём немного
            await asyncio.sleep(2)

            print("Тест завершён")

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"Ошибка HTTP {e.status_code}: {e}")
        print("Попробуйте перезапустить сервер")
    except Exception as e:
        print(f"Ошибка подключения: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(test_stratum())