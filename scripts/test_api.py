# scripts/test_api.py
import requests
import json

BASE_URL = "http://localhost:8000"


def test_endpoint(method, url, params=None, data=None):
    try:
        if method == "GET":
            response = requests.get(f"{BASE_URL}{url}", params=params)
        elif method == "POST":
            response = requests.post(f"{BASE_URL}{url}", params=params)

        print(f"\n{method} {url}")
        print(f"Status: {response.status_code}")
        if response.status_code < 400:  # Измените условие на < 400
            print(f"Response: {json.dumps(response.json(), indent=2)[:200]}...")
        else:
            print(f"Error: {response.text[:200]}")
        return response.status_code < 400
    except Exception as e:
        print(f"Exception: {e}")
        return False


if __name__ == "__main__":
    print("=== Тестирование API эндпоинтов ===")

    endpoints = [
        ("GET", "/"),
        ("GET", "/health"),
        ("GET", "/database/health"),
        ("GET", "/database/tables"),
        ("GET", "/api/v1/miners/"),
        ("GET", "/api/v1/pool/stats"),
        ("GET", "/api/v1/pool/hashrate"),
    ]

    all_ok = True
    for method, url in endpoints:
        if not test_endpoint(method, url):
            all_ok = False

    if all_ok:
        print("\nВсе эндпоинты работают!")
    else:
        print("\nНекоторые эндпоинты не работают")