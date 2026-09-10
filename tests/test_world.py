"""Контракт мира: детерминизм, инварианты, общая лента случайности, скорость."""

import time

import pytest

from contracts import Action, SeedBundle, ShockEvent, ShockParameter
from world import Simulator, build_catalog

HORIZON = 21 * 24
BUDGET = 1_200_000.0


def _uniform_caps(catalog, total=BUDGET, hours=HORIZON):
    return {cid: total / hours / len(catalog.channels) for cid in catalog.channel_ids}


def _run(sim, seeds, scenario="stable", caps=None, hours=HORIZON, budget=BUDGET):
    obs0, _ = sim.reset(seeds, scenario, horizon_hours=hours, total_budget=budget)
    caps = caps or _uniform_caps(sim.catalog, budget, hours)
    rows = []
    for _ in range(hours):
        obs, metrics, done, _ = sim.step(Action(spend_caps=caps))
        rows.append(obs)
    return rows, metrics


def test_same_seed_same_world():
    sim = Simulator(build_catalog(0))
    seeds = SeedBundle(catalog_seed=0, world_seed=3, noise_seed=7)
    a, _ = _run(sim, seeds)
    b, _ = _run(sim, seeds)
    assert [o.model_dump() for o in a] == [o.model_dump() for o in b]


def test_different_noise_seed_changes_world():
    sim = Simulator(build_catalog(0))
    a, _ = _run(sim, SeedBundle(catalog_seed=0, world_seed=3, noise_seed=7))
    b, _ = _run(sim, SeedBundle(catalog_seed=0, world_seed=3, noise_seed=8))
    assert sum(o.total_clicks for o in a) != sum(o.total_clicks for o in b)


def test_invariants_hold_every_hour():
    sim = Simulator(build_catalog(0))
    caps = _uniform_caps(sim.catalog)
    rows, metrics = _run(sim, SeedBundle(catalog_seed=0, world_seed=5, noise_seed=11), caps=caps)
    for obs in rows:
        for cid, ch in obs.by_channel.items():
            assert 0 <= ch.spend <= caps[cid] + 1e-6
            assert 0 <= ch.impressions <= ch.requests
            assert 0 <= ch.unique_reach <= ch.impressions
            assert 0 <= ch.clicks <= ch.impressions
            assert 0 <= ch.conversions <= ch.clicks
            assert ch.ecpm >= 0
    assert metrics.cumulative_spend <= BUDGET + 1e-6


def catalog_is_auction(channel_id: str) -> bool:
    return build_catalog(0).by_id(channel_id).sms is None


def test_zero_cap_gives_zero_spend():
    sim = Simulator(build_catalog(0))
    caps = {cid: 0.0 for cid in sim.catalog.channel_ids}
    rows, metrics = _run(sim, SeedBundle(), caps=caps, hours=48)
    assert metrics.cumulative_spend == 0.0
    assert all(ch.impressions == 0 for o in rows for ch in o.by_channel.values())


def test_huge_cap_saturates_inventory():
    sim = Simulator(build_catalog(0))
    caps = {cid: 1e7 for cid in sim.catalog.channel_ids}
    rows, _ = _run(sim, SeedBundle(), caps=caps, hours=24, budget=1e9)
    for obs in rows:
        for cid, ch in obs.by_channel.items():
            if ch.requests > 0 and catalog_is_auction(cid):
                assert ch.impressions == ch.requests, f"{cid}: при огромном лимите выкупается весь инвентарь"
            elif ch.requests > 0:  # SMS: показы это доставленные, часть сообщений не доходит
                assert ch.impressions <= ch.requests


def test_more_cap_never_fewer_impressions():
    catalog = build_catalog(0)
    sim = Simulator(catalog)
    seeds = SeedBundle(catalog_seed=0, world_seed=2, noise_seed=2)
    low, _ = _run(sim, seeds, caps={cid: 50.0 for cid in catalog.channel_ids}, hours=72, budget=1e7)
    high, _ = _run(sim, seeds, caps={cid: 500.0 for cid in catalog.channel_ids}, hours=72, budget=1e7)
    assert sum(o.total_impressions for o in high) >= sum(o.total_impressions for o in low)


def test_common_random_numbers_are_action_independent():
    """Две стратегии на одном noise_seed видят одну ленту: запросы не зависят от лимитов."""
    catalog = build_catalog(0)
    sim = Simulator(catalog)
    seeds = SeedBundle(catalog_seed=0, world_seed=4, noise_seed=9)
    a, _ = _run(sim, seeds, caps={cid: 100.0 for cid in catalog.channel_ids}, hours=48, budget=1e7)
    b, _ = _run(sim, seeds, caps={cid: 900.0 for cid in catalog.channel_ids}, hours=48, budget=1e7)
    for oa, ob in zip(a, b, strict=True):
        for cid in catalog.channel_ids:
            if catalog.by_id(cid).sms is None:
                assert oa.by_channel[cid].requests == ob.by_channel[cid].requests


def test_shock_applies_only_to_target_channel_and_after_start():
    catalog = build_catalog(0)
    sim = Simulator(catalog)
    seeds = SeedBundle(catalog_seed=0, world_seed=6, noise_seed=6)
    caps = {cid: 300.0 for cid in catalog.channel_ids}
    base, _ = _run(sim, seeds, caps=caps, hours=240, budget=1e7)
    sim.reset(seeds, "stable", horizon_hours=240, total_budget=1e7)
    sim.inject_shock(ShockEvent(start_hour=120, target_channels=["programmatic"], parameter=ShockParameter.PAUSE, multiplier=1.0))
    shocked = [sim.step(Action(spend_caps=caps))[0] for _ in range(240)]
    for h in range(240):
        for cid in catalog.channel_ids:
            if cid == "programmatic" and h >= 120:
                assert shocked[h].by_channel[cid].impressions == 0
            else:
                assert shocked[h].by_channel[cid].model_dump() == base[h].by_channel[cid].model_dump()


def test_invalid_actions_are_rejected():
    sim = Simulator(build_catalog(0))
    sim.reset(SeedBundle(), "stable", horizon_hours=24, total_budget=1000.0)
    with pytest.raises(ValueError):
        sim.step(Action(spend_caps={"social_1": 10.0}))  # не все каналы
    with pytest.raises(ValueError):
        sim.step(Action(spend_caps={cid: 500.0 for cid in sim.catalog.channel_ids}))  # сумма > остатка
    with pytest.raises(ValueError):
        Action(spend_caps={cid: -1.0 for cid in sim.catalog.channel_ids})


def test_full_episode_runs_in_seconds():
    sim = Simulator(build_catalog(0))
    started = time.perf_counter()
    _run(sim, SeedBundle(catalog_seed=0, world_seed=1, noise_seed=1))
    assert time.perf_counter() - started < 3.0


def test_catalog_has_no_hidden_parameters():
    catalog = build_catalog(0)
    payload = catalog.model_dump_json()
    for forbidden in ("world_seed", "noise_seed", "latent", "fatigue", "price_sigma", "shock"):
        assert forbidden not in payload


def test_weekend_is_quieter_than_a_weekday():
    """Выходные отличаются объёмом, а не только формой пика.

    Множитель выходных раньше сокращался посуточной нормировкой профиля, и все дни
    выходили одинаковыми по объёму: раскладывать бюджет по дням было нечего.
    """
    from world.params import draw_hidden_params

    catalog = build_catalog(0)
    for seed in (0, 1, 7):
        hidden = draw_hidden_params(catalog, seed)
        for cid, params in hidden.items():
            profile = params.hourly_profile
            assert abs(profile.sum() - 7.0) < 1e-9, f"{cid}: неделя не нормирована"
            weekday = profile[: 5 * 24].sum() / 5
            weekend = profile[5 * 24 :].sum() / 2
            assert weekend < weekday, f"{cid}, зерно {seed}: выходные не тише будней"
            assert 0.65 < weekend / weekday < 1.0, f"{cid}, зерно {seed}: {weekend / weekday:.3f} вне диапазона допущения"


def test_weekend_ratio_does_not_shift_the_rest_of_the_world():
    """Множитель выходных розыгрывается отдельным потоком: остальные параметры мира на месте.

    Обычный вызов rng сдвинул бы все следующие розыгрыши, и мир того же зерна стал бы
    другим — сравнивать прогоны между версиями было бы нечем.
    """
    from world.params import draw_hidden_params

    catalog = build_catalog(0)
    hidden = draw_hidden_params(catalog, 0)
    # значения зафиксированы на сборке, где множитель выходных ещё не розыгрывался
    assert abs(hidden["social_1"].daily_requests - 151_672.26758935125) < 1e-6
    assert abs(hidden["sms"].daily_requests - 9142.857142857143) < 1e-6
