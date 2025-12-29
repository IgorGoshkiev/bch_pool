import requests
import json

BASE_URL = "http://localhost:8000"


def test_miners_api():
    print("=== Тестирование API майнеров ===")

    test_address = "wwwwwwwwwwwwwwwwwwwwwwwwww"

    # 1. Получить статистику майнера
    print("\n1. GET /api/v1/miners/{bch_address}/stats")
    response = requests.get(f"{BASE_URL}/api/v1/miners/{test_address}/stats")
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        print(f"   Response OK")

    # 2. Обновить данные майнера
    print("\n2. PUT /api/v1/miners/{bch_address}/update")
    response = requests.put(
        f"{BASE_URL}/api/v1/miners/{test_address}/update",
        params={"worker_name": "updated_worker"}
    )
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        print(f"   Miner updated")

    # 3. Получить список шаров (пустой сейчас)
    print("\n3. GET /api/v1/miners/{bch_address}/shares")
    response = requests.get(f"{BASE_URL}/api/v1/miners/{test_address}/shares")
    print(f"   Status: {response.status_code}")

    # 4. Получить список блоков (пустой сейчас)
    print("\n4. GET /api/v1/miners/{bch_address}/blocks")
    response = requests.get(f"{BASE_URL}/api/v1/miners/{test_address}/blocks")
    print(f"   Status: {response.status_code}")

    # 5. Деактивировать майнера (тестовый - не выполняем чтобы не потерять данные)
    print("\n5. DELETE /api/v1/miners/{bch_address} (закомментировано)")
    # response = requests.delete(f"{BASE_URL}/api/v1/miners/{test_address}")
    # print(f"   Status: {response.status_code}")


if __name__ == "__main__":
    test_miners_api()