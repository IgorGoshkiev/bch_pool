cd / home / user - noda / bch_pool
source
venv / bin / activate
python3 << 'EOF'
import asyncio
import subprocess
import json
import hashlib
from app.jobs.manager import JobManager
from app.dependencies import block_builder, job_service, share_validator


async def test_share_validation():
    """Тест: валидация шара с правильным хэшем"""
    print("\n=== ТЕСТ 5: ВАЛИДАЦИЯ ШАРА ===\n")

    job_manager = JobManager(job_service=job_service, block_builder=block_builder)
    await job_manager.initialize()

    # Получаем реальный блок для теста
    result = subprocess.run(['bitcoin-cli', 'getbestblockhash'], capture_output=True, text=True)
    bestblockhash = result.stdout.strip()

    result = subprocess.run(['bitcoin-cli', 'getblockheader', bestblockhash], capture_output=True, text=True)
    header_data = json.loads(result.stdout)

    print(f"Тестовый блок: {bestblockhash[:32]}...")
    print(f"Bits: {header_data['bits']}")
    print(f"Nonce: {header_data['nonce']}")
    print(f"Time: {header_data['time']}")

    # Создаем задание
    template = await job_manager.node_client.get_block_template()
    extra_nonce1 = await job_manager.node_client.get_extra_nonce1()

    miner_address = "qqxsgzrcxvwh3emhrzmgedttm3ju6ks4ec6072chl0"

    # Создаем job_data для валидатора
    job_data = block_builder.create_stratum_job_data(
        template=template,
        job_id="test_job_001",
        miner_address=miner_address,
        extra_nonce1=extra_nonce1
    )

    if not job_data:
        print("❌ Не удалось создать job_data")
        return

    # Добавляем в валидатор
    job_id = "test_job_001"
    share_validator.add_job(job_id, job_data)

    # Проверяем, что валидатор может рассчитать хэш
    extra_nonce2 = "00000000"
    ntime = format(header_data['time'], '08x')
    nonce = format(header_data['nonce'], '08x')

    try:
        hash_result = share_validator.calculate_hash(
            job_data, extra_nonce2, ntime, nonce
        )
        print(f"\nРасчетный хэш: {hash_result[:32]}...")
        print(f"Реальный хэш: {bestblockhash[:32]}...")

        if hash_result == bestblockhash:
            print("✅ Хэш совпадает с реальным блоком!")
        else:
            print("ℹ️ Хэш не совпадает (ожидаемо, так как это тестовый nonce)")

        # Проверяем сложность
        hash_int = int(hash_result, 16)
        target_for_diff_1 = share_validator.TARGET_FOR_DIFFICULTY_1
        share_difficulty = target_for_diff_1 // hash_int

        print(f"\nСложность шара: {share_difficulty}")

        if share_difficulty > 0:
            print("✅ Сложность рассчитана корректно")
        else:
            print("⚠️ Сложность = 0 (шар слишком легкий)")

    except Exception as e:
        print(f"❌ Ошибка при расчете хэша: {e}")


async def test_extra_nonce1_dynamic():
    """Тест: динамическое получение extra_nonce1 из ноды"""
    print("\n=== ТЕСТ 6: ДИНАМИЧЕСКИЙ EXTRA_NONCE1 ===\n")

    job_manager = JobManager(job_service=job_service, block_builder=block_builder)
    await job_manager.initialize()

    # Получаем несколько раз, проверяем что не меняется
    extra1 = await job_manager.node_client.get_extra_nonce1()
    print(f"EXTRA_NONCE1 (первый раз): {extra1}")

    # Обновляем шаблон
    await job_manager.node_client.get_block_template()
    extra2 = await job_manager.node_client.get_extra_nonce1()
    print(f"EXTRA_NONCE1 (второй раз): {extra2}")

    if extra1 == extra2:
        print("✅ EXTRA_NONCE1 стабилен (одинаковый)")
    else:
        print("ℹ️ EXTRA_NONCE1 изменился (это нормально при смене блока)")


async def test_coinbase_structure():
    """Тест: структура coinbase транзакции"""
    print("\n=== ТЕСТ 7: СТРУКТУРА COINBASE ===\n")

    job_manager = JobManager(job_service=job_service, block_builder=block_builder)
    await job_manager.initialize()

    template = await job_manager.node_client.get_block_template()
    extra_nonce1 = await job_manager.node_client.get_extra_nonce1()

    miner_address = "qqxsgzrcxvwh3emhrzmgedttm3ju6ks4ec6072chl0"
    coinbase_hex, coinbase_txid, merkle_branch_json = block_builder.build_coinbase_transaction(
        template=template,
        miner_address=miner_address,
        extra_nonce1=extra_nonce1,
        extra_nonce2="00000000"
    )

    print(f"Coinbase TXID: {coinbase_txid}")
    print(f"Coinbase длина: {len(coinbase_hex)} байт")

    # Проверяем структуру
    # 1. Версия транзакции (первые 8 символов)
    version = coinbase_hex[:8]
    print(f"\nВерсия транзакции: {version} (должно быть 01000000)")
    print("✅" if version == "01000000" else "❌")

    # 2. Количество входов (следующие 2 символа)
    input_count = coinbase_hex[8:10]
    print(f"Количество входов: {input_count} (должно быть 01)")
    print("✅" if input_count == "01" else "❌")

    # 3. Проверяем, что есть coinbase (нулевой хэш)
    zero_hash = coinbase_hex[10:74]
    print(f"\nPrevout hash (первые 16): {zero_hash[:16]}... (должно быть 00...00)")
    is_zero = all(c == '0' for c in zero_hash)
    print("✅" if is_zero else "❌")

    # 4. Проверяем sequence (ffffffff)
    seq_pos = coinbase_hex.find('ffffffff')
    print(f"\nSequence найден на позиции: {seq_pos}")
    print("✅" if seq_pos > 0 else "❌")

    # 5. Проверяем выходы
    output_pos = coinbase_hex.find('ffffffff', seq_pos + 8)
    if output_pos > 0:
        print(f"Второй ffffffff (начало выходов) на позиции: {output_pos}")
        print("✅ Структура корректна")
    else:
        print("⚠️ Второй ffffffff не найден")


async def test_job_service_get_job():
    """Тест: получение задания из job_service"""
    print("\n=== ТЕСТ 8: JOB_SERVICE GET_JOB ===\n")

    job_manager = JobManager(job_service=job_service, block_builder=block_builder)
    await job_manager.initialize()

    # Создаем задание
    job_data = await job_manager.create_new_job("qqxsgzrcxvwh3emhrzmgedttm3ju6ks4ec6072chl0")

    if not job_data:
        print("❌ Не удалось создать задание")
        return

    job_id = job_data['params'][0]
    print(f"Создано задание: {job_id}")

    # Получаем через job_service
    retrieved_job = job_service.get_job(job_id)

    if retrieved_job:
        print(f"✅ Задание найдено в job_service")
        print(f"   Job ID: {retrieved_job['params'][0]}")
        print(f"   extra_nonce1: {retrieved_job.get('extra_nonce1')}")
    else:
        print(f"❌ Задание {job_id} НЕ найдено в job_service")


async def main():
    print("=" * 60)
    print("ЗАПУСК ДОПОЛНИТЕЛЬНЫХ ТЕСТОВ")
    print("=" * 60)

    await test_share_validation()
    await test_extra_nonce1_dynamic()
    await test_coinbase_structure()
    await test_job_service_get_job()

    print("\n" + "=" * 60)
    print("ВСЕ ДОПОЛНИТЕЛЬНЫЕ ТЕСТЫ ЗАВЕРШЕНЫ")
    print("=" * 60)


asyncio.run(main())
EOF