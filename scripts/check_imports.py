import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

print("=== ПРОВЕРКА ИМПОРТОВ ===")

try:
    print("1. Импортируем database...")
    from app.models.database import Base
    print("  database OK")
except Exception as e:
    print(f"  database error: {e}")

try:
    print("2. Импортируем miner model...")
    from app.models.miner import Miner
    print("  miner model OK")
except Exception as e:
    print(f"  miner error: {e}")

try:
    print("3. Импортируем miners router...")
    from app.api.v1.miners import router
    print("  miners router OK")
except Exception as e:
    print(f"   miners router error: {e}")

try:
    print("4. Импортируем pool router...")
    from app.api.v1.pool import router as pool_router
    print("  pool router OK")
except Exception as e:
    print(f"  pool router error: {e}")

print("\n=== ПРОВЕРКА ЗАВЕРШЕНА ===")