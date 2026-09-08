"""География кампании: федеральные округа, доля аудитории, пересчёт ёмкости.

Кабинетный слой. ``brain`` и ``world`` про регионы ничего не знают, и контракт
брифа мы не трогаем: география живёт здесь и выражается одним честным
действием — ёмкость каналов пересчитывается на долю аудитории выбранных
округов. Тот же пересчитанный каталог уходит и в план, и в прогон, иначе план
и факт считались бы о разных мирах.

Чего этот слой не делает: не моделирует региональные цены, пересечение
аудиторий соседних округов и разное проникновение интернета. Деление бюджета
между округами — плановая величина: симулятор ведёт кампанию целиком.
Запрос владельцу мира записан в docs/cabinet_changes.md.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from contracts import PublicCatalog

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "geo.yaml"


@dataclass(frozen=True)
class District:
    """Федеральный округ: доля считается от численности всех округов реестра."""

    id: str
    title: str
    center: str
    population: int
    weight: float
    federal_cities: tuple[str, ...]
    million_cities: tuple[str, ...]


def _load() -> tuple[dict[str, District], dict]:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    entries = raw["districts"]
    total = sum(e["population"]["value"] for e in entries.values())
    districts = {
        key: District(
            id=key,
            title=entry["title"],
            center=entry["center"],
            population=entry["population"]["value"],
            weight=entry["population"]["value"] / total,
            federal_cities=tuple(entry.get("federal_cities", [])),
            million_cities=tuple(entry.get("million_cities", [])),
        )
        for key, entry in entries.items()
    }
    return districts, raw["meta"]


DISTRICTS, GEO_META = _load()
ALL_REGIONS: tuple[str, ...] = tuple(DISTRICTS)


def normalize(region_ids: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Пусто или все округа — вся Россия; порядок всегда реестровый.

    Порядок важен: по нему считается ключ кэша каталога и подпись в кабинете.
    """
    if not region_ids:
        return ALL_REGIONS
    unknown = [r for r in region_ids if r not in DISTRICTS]
    if unknown:
        raise ValueError(f"неизвестные округа: {', '.join(unknown)}")
    return tuple(r for r in ALL_REGIONS if r in set(region_ids))


def audience_share(region_ids: list[str] | tuple[str, ...] | None) -> float:
    """Доля населения выбранных округов от всей страны: 1.0 — вся Россия."""
    regions = normalize(region_ids)
    if regions == ALL_REGIONS:
        return 1.0
    return sum(DISTRICTS[r].weight for r in regions)


def scale_catalog(catalog: PublicCatalog, share: float) -> PublicCatalog:
    """Каталог с ёмкостью, пересчитанной на долю аудитории.

    Меняются только ёмкости: дневная уникальная аудитория канала и абонентская
    база SMS. Цены и качество трафика от географии в этой модели не зависят —
    так честнее, чем выдумывать региональные надбавки без источника.
    """
    if share >= 1.0:
        return catalog
    channels = []
    for channel in catalog.channels:
        low, high = channel.daily_unique_capacity_band
        updates: dict[str, object] = {
            "daily_unique_capacity_band": (max(1, round(low * share)), max(1, round(high * share)))
        }
        if channel.sms is not None:
            updates["sms"] = channel.sms.model_copy(update={"base_size": max(1, round(channel.sms.base_size * share))})
        channels.append(channel.model_copy(update=updates))
    # свой идентификатор: по нему считается plan_id, иначе планы для разной географии совпали бы
    catalog_id = hashlib.sha256(f"{catalog.catalog_id}:{share:.6f}".encode()).hexdigest()[:16]
    return catalog.model_copy(update={"catalog_id": catalog_id, "channels": channels})


def split_budget(
    total_rub: float,
    region_ids: list[str] | tuple[str, ...] | None,
    manual: dict[str, float] | None = None,
) -> list[dict[str, object]]:
    """Деление бюджета по округам: по населению или по рукам пользователя.

    ``manual`` — доли (0..1 или проценты, нормируем сами) по округам выбора.
    """
    regions = normalize(region_ids)
    base = {r: DISTRICTS[r].weight for r in regions}
    base_sum = sum(base.values()) or 1.0
    shares = {r: w / base_sum for r, w in base.items()}
    manual_used = False
    if manual:
        picked = {r: max(0.0, float(v)) for r, v in manual.items() if r in shares}
        if picked and sum(picked.values()) > 0:
            manual_sum = sum(picked.values())
            shares = {r: picked.get(r, 0.0) / manual_sum for r in regions}
            manual_used = True
    return [
        {
            "id": r,
            "title": DISTRICTS[r].title,
            "center": DISTRICTS[r].center,
            "population": DISTRICTS[r].population,
            "share": shares[r],
            "budget_rub": round(total_rub * shares[r], 2),
            "manual": manual_used,
        }
        for r in regions
    ]


def geo_view(
    total_rub: float,
    region_ids: list[str] | tuple[str, ...] | None,
    manual: dict[str, float] | None = None,
) -> dict[str, object]:
    """Блок географии для ответа API: что выбрано, сколько это аудитории, как поделён бюджет."""
    regions = normalize(region_ids)
    share = audience_share(regions)
    return {
        "regions": list(regions),
        "all_russia": regions == ALL_REGIONS,
        "audience_share": share,
        "split": split_budget(total_rub, regions, manual),
        "manual_split": bool(manual) and any(r in set(regions) for r in manual),
    }


def catalog_meta() -> dict[str, object]:
    """Справочник округов для кабинета: имена, центры, доли аудитории."""
    return {
        "districts": [
            {
                "id": d.id,
                "title": d.title,
                "center": d.center,
                "population": d.population,
                "weight": d.weight,
                "federal_cities": list(d.federal_cities),
                "million_cities": list(d.million_cities),
            }
            for d in DISTRICTS.values()
        ],
        "population_as_of": GEO_META["population_as_of"],
        "population_as_of_title": GEO_META["population_as_of_title"],
        "source_url": GEO_META["source_url"],
        "map_source": GEO_META["map_source"],
        "cities_source_url": GEO_META["cities_source_url"],
    }
