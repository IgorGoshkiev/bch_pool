import socket
import base64
import json


def test_tunnel_with_auth():
    """Тест SSH туннеля с правильной авторизацией RPC"""
    print("Тестируем подключение к BCH ноде через SSH туннель...")

    # Данные из вашего bitcoin.conf на сервере
    RPC_USER = "rpctestuser"
    RPC_PASSWORD = "firebird"

    # Подготовка HTTP запроса с Basic Auth
    auth = base64.b64encode(f"{RPC_USER}:{RPC_PASSWORD}".encode()).decode()

    # JSON-RPC запрос
    payload = {
        "jsonrpc": "1.0",
        "id": "test",
        "method": "getblockcount",
        "params": []
    }

    # HTTP запрос
    request = (
        "POST / HTTP/1.1\r\n"
        f"Host: 127.0.0.1:28332\r\n"
        "Content-Type: application/json\r\n"
        f"Authorization: Basic {auth}\r\n"
        f"Content-Length: {len(json.dumps(payload))}\r\n"
        "\r\n"
        f"{json.dumps(payload)}"
    )

    try:
        # Подключаемся
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(('127.0.0.1', 28332))

        print("✅ Подключено к порту 28332")

        # Отправляем запрос
        sock.sendall(request.encode())
        print("✅ Запрос отправлен")

        # Получаем ответ
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\r\n\r\n" in response:  # Конец заголовков
                break

        sock.close()

        # Парсим ответ
        if response:
            # Разделяем заголовки и тело
            headers_body = response.split(b"\r\n\r\n", 1)
            headers = headers_body[0].decode()
            body = headers_body[1].decode() if len(headers_body) > 1 else ""

            print(f"\n📥 Ответ от ноды:")
            print(f"Заголовки:\n{headers[:200]}...")

            if body:
                try:
                    data = json.loads(body)
                    if "result" in data:
                        print(f"✅ Успех! Высота блокчейна: {data['result']}")
                        return True
                    elif "error" in data:
                        print(f"❌ Ошибка RPC: {data['error']}")
                        return False
                except json.JSONDecodeError:
                    print(f"Тело ответа: {body[:200]}...")
        else:
            print("❌ Нет ответа от ноды")

    except ConnectionRefusedError:
        print("❌ Не удалось подключиться к порту 28332")
        print("   Убедитесь что SSH туннель запущен")
    except socket.timeout:
        print("❌ Таймаут при подключении")
    except Exception as e:
        print(f"❌ Ошибка: {type(e).__name__}: {e}")

    return False


if __name__ == "__main__":
    print("=" * 60)
    print("ТЕСТ ПОДКЛЮЧЕНИЯ К BCH НОДЕ ЧЕРЕЗ SSH ТУННЕЛЬ")
    print("=" * 60)
    print("\nПРЕДВАРИТЕЛЬНЫЕ УСЛОВИЯ:")
    print("1. SSH туннель запущен в отдельном окне:")
    print("   ssh -L 28332:localhost:28332 pooladmin@192.168.10.142 -N")
    print("2. Нода запущена на сервере")
    print("3. В bitcoin.conf настроен RPC_USER и RPC_PASSWORD")
    print("=" * 60)

    success = test_tunnel_with_auth()

    print("\n" + "=" * 60)
    if success:
        print("✅ ТЕСТ ПРОЙДЕН! Нода доступна через SSH туннель")
        print("\nТеперь можно запустить пул:")
        print("uvicorn app.main:app --reload --port 8000")
    else:
        print("❌ ТЕСТ НЕ ПРОЙДЕН")
        print("\nПРОВЕРЬТЕ:")
        print("1. Правильность RPC_USER/RPC_PASSWORD в bitcoin.conf")
        print("2. Что нода запущена: ps aux | grep bitcoind")
        print("3. Настройки в bitcoin.conf:")
        print("   server=1")
        print("   rpcallowip=127.0.0.1")
        print("   rpcuser=rpctestuser")
        print("   rpcpassword=firebird")
    print("=" * 60)