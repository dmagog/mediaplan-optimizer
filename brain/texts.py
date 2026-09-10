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


def days_word_gen(n: int) -> str:
    """Родительный падеж после предлога: «до 21 дня», «до 23 дней»."""
    if 11 <= n % 100 <= 14:
        return "дней"
    return "дня" if n % 10 == 1 else "дней"


def kpi_word(n: float, kpi: str) -> str:
    """Согласование с числом: «533 конверсии», «21 конверсия», «530 конверсий»."""
    forms = {
        "conversions": ("конверсия", "конверсии", "конверсий"),
        "clicks": ("клик", "клика", "кликов"),
        "reach": ("охват", "охвата", "охвата"),
    }.get(kpi)
    if forms is None:
        return kpi
    k = abs(int(round(n)))
    if 11 <= k % 100 <= 14:
        return forms[2]
    return {1: forms[0], 2: forms[1], 3: forms[1], 4: forms[1]}.get(k % 10, forms[2])


def rub(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ") + " ₽"


def price(x: float) -> str:
    """Цена за результат: до десяти рублей с копейками, иначе рублями.

    Цена охвата и клика меряется копейками, и округление до рубля печатало «0 ₽»
    там, где человек задал 5 копеек.
    """
    if abs(x) < 10:
        return f"{x:.2f}".replace(".", ",") + " ₽"
    return rub(x)


def num(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")
