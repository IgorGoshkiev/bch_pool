import socket
import time


def test_tunnel():
    """Простой тест SSH туннеля"""
    print("Тестируем SSH туннель на порту 28332...")

    try:
        # Пробуем подключиться
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)

        result = sock.connect_ex(('127.0.0.1', 28332))

        if result == 0:
            print("Порт 28332 открыт и слушает!")

            # Пробуем отправить тестовые данные
            try:
                sock.send(b'{"jsonrpc":"1.0","id":"test","method":"getblockcount"}\n')
                response = sock.recv(1024)
                if response:
                    print(f"Получен ответ от ноды: {response[:100]}...")
                else:
                    print("Порт открыт, но нода не отвечает")
            except:
                print("Порт открыт, но нода не принимает данные")
        else:
            print(f"Порт 28332 закрыт (код ошибки: {result})")
            print("\nВозможные причины:")
            print("1. SSH туннель не запущен")
            print("2. Неверный пароль SSH")
            print("3. Нода не запущена на сервере")
            print("4. Firewall блокирует соединение")

        sock.close()

    except Exception as e:
        print(f"Ошибка: {e}")


if __name__ == "__main__":
    print("=" * 50)
    print("ТЕСТ SSH ТУННЕЛЯ ДЛЯ BCH НОДЫ")
    print("=" * 50)
    print("\nПРЕДВАРИТЕЛЬНЫЕ ШАГИ:")
    print("1. Запустите SSH туннель в отдельном окне:")
    print("   ssh -L 28332:localhost:28332 pooladmin@192.168.10.142 -N")
    print("2. Введите пароль когда запросит")
    print("3. Затем запустите этот тест в ДРУГОМ окне")
    print("=" * 50)

    if input("\nТуннель запущен? (y/n): ").lower() == 'y':
        test_tunnel()
    else:
        print("Сначала запустите туннель!")