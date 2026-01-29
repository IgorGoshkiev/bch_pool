import pytest
from app.dependencies import DependencyContainer


def test_bch_address_in_container():
    """Тест что BCHAddress доступен в контейнере"""
    container = DependencyContainer()

    # Должен создаться лениво
    assert container._bch_address is None

    # При первом обращении создается
    bch_addr = container.bch_address
    assert bch_addr is not None
    assert container._bch_address is not None

    # Должен иметь метод validate
    assert hasattr(bch_addr, 'validate')

    # Проверяем статистику
    stats = container.get_stats()
    assert 'bch_address' in stats
    assert stats['bch_address'] is True


def test_bch_address_validate():
    """Тест валидации через контейнер"""
    from app.dependencies import bch_address

    # Валидные адреса
    assert bch_address.validate("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a")[0]
    assert bch_address.validate("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")[0]

    # Невалидные адреса
    assert not bch_address.validate("")[0]
    assert not bch_address.validate("invalid_address")[0]