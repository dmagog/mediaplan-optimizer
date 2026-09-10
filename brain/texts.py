"""Человеческие тексты мозга: имена KPI по-русски и числа с пробелами вместо запятых."""

KPI_LABELS = {"conversions": "конверсий", "clicks": "кликов", "reach": "охвата"}


# за что платим: единственное число для цены за результат
KPI_UNITS = {"conversions": "конверсию", "clicks": "клик", "reach": "охваченного человека"}


def kpi_label(kpi: str) -> str:
    return KPI_LABELS.get(kpi, kpi)


def kpi_unit(kpi: str) -> str:
    return KPI_UNITS.get(kpi, kpi)


def days_word(n: int) -> str:
    """«21 день», «22 дня», «25 дней»: срок из брифа попадает в тексты как есть."""
    if 11 <= n % 100 <= 14:
        return "дней"
    return {1: "день", 2: "дня", 3: "дня", 4: "дня"}.get(n % 10, "дней")


def rub(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ") + " ₽"


def num(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")
