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