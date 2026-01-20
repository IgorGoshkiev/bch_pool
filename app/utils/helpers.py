from datetime import datetime, UTC


def humanize_time_ago(dt: datetime) -> str:
    """Форматирует время в человекочитаемый вид"""
    if not dt:
        return "никогда"

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    now = datetime.now(UTC)
    diff = now - dt

    if diff.days > 365:
        return f"{diff.days // 365} лет назад"
    elif diff.days > 30:
        return f"{diff.days // 30} месяцев назад"
    elif diff.days > 0:
        return f"{diff.days} дней назад"
    elif diff.seconds > 3600:
        return f"{diff.seconds // 3600} часов назад"
    elif diff.seconds > 60:
        return f"{diff.seconds // 60} минут назад"
    else:
        return f"{diff.seconds} секунд назад"


def calculate_pagination_info(skip: int, limit: int, total: int, current_count: int):
    """
    Рассчитывает информацию о пагинации

    Args:
        skip: Пропущено записей
        limit: Лимит на странице
        total: Всего записей
        current_count: Количество на текущей странице

    Returns:
        Словарь с информацией о пагинации
    """
    current_page = (skip // limit) + 1 if limit > 0 else 1
    total_pages = (total + limit - 1) // limit if limit > 0 else 1

    return {
        "skip": skip,
        "limit": limit,
        "total": total,
        "current_page": current_page,
        "total_pages": total_pages,
        "has_next": (skip + current_count) < total,
        "has_prev": skip > 0,
        "returned": current_count
    }