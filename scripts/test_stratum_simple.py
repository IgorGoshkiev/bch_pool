import asyncio
import websockets
import json


async def test():
    uri = "ws://127.0.0.1:8000/stratum/ws/test_miner"

    try:
        async with websockets.connect(uri) as ws:
            print("Подключились")

            # 1. Получаем приветствие
            welcome = await ws.recv()
            print(f"Приветствие: {welcome}")

            # 2. Отправляем подписку
            await ws.send(json.dumps({
                "id": 1,
                "method": "mining.subscribe",
                "params": []
            }))
            print("Отправили подписку")

            # 3. Ждем ответ
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=3.0)
                print(f"Ответ на подписку: {response}")
            except asyncio.TimeoutError:
                print("⚠Нет ответа на подписку")
                return False

            # 4. Авторизация
            await ws.send(json.dumps({
                "id": 2,
                "method": "mining.authorize",
                "params": ["worker1", "x"]
            }))
            print("Отправили авторизацию")

            # 5. Ждем ответ
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=3.0)
                print(f"Ответ на авторизацию: {response}")
            except asyncio.TimeoutError:
                print("Нет ответа на авторизацию")
                return False

            print(" Все сообщения обработаны!")
            return True

    except Exception as e:
        print(f"Ошибка: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    success = asyncio.run(test())
    if success:
        print("\nStratum протокол работает!")
    else:
        print("\nПроверьте обработку сообщений в stratum_server")