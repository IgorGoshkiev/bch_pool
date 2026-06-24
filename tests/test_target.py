from decimal import Decimal

def calculate_target():
    target_for_difficulty_1 = 0x0000000000000000024cb3000000000000000000000000000000000000000000
    pool_difficulty = 65536

    target = target_for_difficulty_1 // pool_difficulty

    print(f"target_for_difficulty_1 = {target_for_difficulty_1}")
    print(f"pool_difficulty = {pool_difficulty}")
    print(f"target = {target}")
    print(f"target hex = {target:#066x}")

    # Проверка с Decimal

    target_decimal = int(Decimal(target_for_difficulty_1) / Decimal(pool_difficulty))
    print(f"target_decimal hex = {target_decimal:#066x}")

    # Правильное значение для 65536
    correct_target = 0x000000000000000000024cb300000000000000000000000000000000000000
    print(f"correct_target hex = {correct_target:#066x}")
    print(f"match = {target == correct_target}")


if __name__ == "__main__":
    calculate_target()