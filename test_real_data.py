# test_real_data.py - ФИНАЛЬНАЯ ВЕРСИЯ

import asyncio
import sys

sys.path.insert(0, '/home/user-noda/bch_pool')

from app.utils.config import settings
from app.jobs.real_node_client import RealBCHNodeClient
from app.stratum.validator import ShareValidator
from app.utils.protocol_helpers import STRATUM_EXTRA_NONCE1


async def test_node_connection():
    print("=" * 70)
    print("ДИАГНОСТИКА BCH SOLO ПУЛА (MAINNET)")
    print("=" * 70)

    # 1. Подключаемся к ноде
    print("\n1. ПОДКЛЮЧЕНИЕ К НОДЕ:")
    node = RealBCHNodeClient(
        rpc_host=settings.bch_rpc_host,
        rpc_port=settings.bch_rpc_port,
        rpc_user=settings.bch_rpc_user,
        rpc_password=settings.bch_rpc_password,
        use_cookie=settings.bch_rpc_use_cookie
    )

    if not await node.connect():
        print("   ❌ НЕ УДАЛОСЬ ПОДКЛЮЧИТЬСЯ!")
        return False

    print("   ✅ Подключено успешно")

    # 2. Получаем шаблон блока
    print("\n2. ШАБЛОН БЛОКА:")
    template = await node.get_block_template()

    if not template:
        print("   ❌ НЕТ ШАБЛОНА!")
        return False

    prev_hash = template.get('previousblockhash', '')
    height = template.get('height', 0)
    bits = template.get('bits', '')
    coinbase_value = template.get('coinbasevalue', 0)

    print(f"   Height: {height}")
    print(f"   Prev hash: {prev_hash[:32]}...")
    print(f"   Bits: {bits}")
    print(f"   Reward: {coinbase_value / 1e8} BCH")

    # 3. Получаем сложность сети
    print("\n3. СЛОЖНОСТЬ СЕТИ:")
    mining_info = await node.get_mining_info()
    network_diff = mining_info.get('difficulty', 1.0) if mining_info else 1.0
    print(f"   Network difficulty: {network_diff:,.0f}")

    # 4. Создаем валидатор
    print("\n4. ВАЛИДАТОР:")
    pool_diff = settings.default_share_difficulty
    validator = ShareValidator(
        pool_difficulty=pool_diff,
        extra_nonce2_size=4,
        extra_nonce1=STRATUM_EXTRA_NONCE1
    )

    print(f"   Pool difficulty: {pool_diff}")
    print(f"   Target for difficulty 1: {hex(validator.TARGET_FOR_DIFFICULTY_1)}")

    # 5. Анализ сложности
    print("\n5. АНАЛИЗ СЛОЖНОСТИ:")

    # Target для difficulty 1
    target_1 = validator.TARGET_FOR_DIFFICULTY_1

    # Target для пула
    if pool_diff > 0:
        target_pool = target_1 // int(pool_diff) if pool_diff >= 1 else target_1
        print(f"   Pool target (diff={pool_diff}): {target_pool:#066x}")

    # Target для сети
    target_network = target_1 // int(network_diff)
    print(f"   Network target (diff={network_diff:,.0f}): {target_network:#066x}")

    # Соотношение
    ratio = network_diff / pool_diff if pool_diff > 0 else 0
    print(f"\n   📊 СООТНОШЕНИЕ: Network сложность в {ratio:,.0f} раз ВЫШЕ pool сложности")

    if ratio > 1000000:
        print(f"   ⚠️  Это ОГРОМНАЯ разница! Майнеру нужно найти блок,")
        print(f"      а не просто шар. Для соло-майнинга это нормально,")
        print(f"      но сложность должна быть адекватной.")

    # 6. Проверяем задание от JobManager
    print("\n6. ПРОВЕРКА JOBMANAGER:")
    from app.jobs.manager import JobManager
    from app.stratum.block_builder import BlockBuilder
    from app.utils.network_config import NetworkManager
    from app.services.job_service import JobService

    network_manager = NetworkManager()
    block_builder = BlockBuilder(network_manager=network_manager)
    job_service = JobService(validator=validator, network_manager=network_manager)

    job_manager = JobManager(
        job_service=job_service,
        block_builder=block_builder
    )

    if await job_manager.initialize():
        print("   ✅ JobManager инициализирован")

        job_data = await job_manager.create_new_job()

        if job_data:
            params = job_data.get('params', [])
            if len(params) >= 2:
                job_prevhash = params[1]
                print(f"\n   📦 ЗАДАНИЕ ОТ JOBMANAGER:")
                print(f"   Job ID: {params[0]}")
                print(f"   Prevhash: {job_prevhash[:32]}...")

                if job_prevhash == '0' * 64:
                    print(f"   ❌ PREVHASH = 0! Задание фейковое!")
                    print(f"   Проблема в get_block_template от ноды")
                elif job_prevhash == prev_hash:
                    print(f"   ✅ PREVHASH СОВПАДАЕТ с шаблоном ноды!")
                    print(f"   Задание РЕАЛЬНОЕ, можно майнить!")
                else:
                    print(f"   ⚠️  Prevhash не совпадает: {job_prevhash[:32]}...")
        else:
            print("   ❌ Не удалось создать задание")
    else:
        print("   ❌ Не удалось инициализировать JobManager")

    print("\n" + "=" * 70)

    # 7. РЕКОМЕНДАЦИИ
    print("\n📋 РЕКОМЕНДАЦИИ:")
    print(f"   1. Ты на MAINNET с сложностью {network_diff:,.0f}")
    print(f"   2. Это ОЧЕНЬ ВЫСОКАЯ сложность для соло-майнинга")
    print(f"   3. Шанс найти блок с одним ASIC - крайне мал")
    print(f"   4. Для тестов лучше использовать TESTNET")
    print(f"   5. Если хочешь mainnet - настрой пул с PPLNS/пропорциональной системой")

    return True


if __name__ == "__main__":
    print("\n🚀 ЗАПУСК ДИАГНОСТИКИ...\n")
    asyncio.run(test_node_connection())