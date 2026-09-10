"""Кабинет: три демонстрации кейса через API и правила, важные для показа.

Проверяем не математику (она в тестах brain/world), а контракт кабинета:
что демо 1 отдаёт всё, что требует кейс; что демо 2 отказывает с тремя ходами;
что прогон возможен только для утверждённого плана; что любая ошибка приходит
по-русски как JSON, а не как 500.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

DEMO1 = {
    "mode": "A",
    "preset": "all",
    "budget_rub": 1_200_000,
    "horizon_days": 21,
    "objective": "max_conversions",
    "automation_limit_rub": 50_000,  # с новым исполнителем переносы первого дня ниже 100 000, карточка ждёт решения при 50 000
}
DEMO2 = {"mode": "B", "preset": "narrow", "target_kpi": "clicks", "target_value": 50_000, "horizon_days": 14}
SIX_METRICS = ("ctr", "cvr", "cpm_rub", "cpc_rub", "cpa_rub", "vtr")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # контекст запускает lifespan: каталог, ретро, кривые
        yield c


def _plan(client: TestClient, body: dict) -> dict:
    r = client.post("/api/plan", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _approved_plan(client: TestClient, body: dict) -> dict:
    plan = _plan(client, body)
    r = client.post(f"/api/plan/{plan['plan_id']}/approve")
    assert r.status_code == 200, r.text
    return plan


def test_demo1_plan_has_everything_case_asks(client):
    plan = _plan(client, DEMO1)
    assert plan["infeasibility"] is None
    assert len(plan["allocations"]) == 8
    assert abs(plan["total_budget_rub"] - 1_200_000) < 1.0
    for a in plan["allocations"]:
        for key in SIX_METRICS:
            assert key in a, f"в строке плана нет {key}"
        assert a["display_name"] and not a["display_name"].startswith(a["channel_id"])
    assert any(a["marginal_cost_per_1000_kpi_rub"] for a in plan["allocations"]), "цена следующей тысячи не посчитана"
    assert len(plan["calendar"]) == 8 * 21
    assert len(plan["trajectory"]) == 21 * 24
    f = plan["forecast"]
    assert f["p10"] < f["p50"] < f["p90"]
    assert plan["trajectory"][-1]["cum_conversions"] == pytest.approx(plan["total_kpi"])


def test_demo2_refuses_with_three_moves_cheapest_first(client):
    plan = _plan(client, DEMO2)
    inf = plan["infeasibility"]
    assert inf is not None
    assert inf["max_achievable"] < 50_000
    assert inf["binding_title"] and inf["binding_title"] != inf["binding_constraint"]
    budgets = [s["expected_budget_rub"] for s in inf["suggestions"]]
    assert len(budgets) == 3
    assert budgets == sorted(budgets), "ходы должны идти от дешёвого к дорогому"
    fields = {s["changed_field"] for s in inf["suggestions"]}
    assert fields == {"horizon_days", "target_value", "channel_ids"}


def test_infeasible_plan_cannot_be_approved_or_run(client):
    plan = _plan(client, DEMO2)
    assert client.post(f"/api/plan/{plan['plan_id']}/approve").status_code == 409
    r = client.post("/api/run", json={"plan_id": plan["plan_id"]})
    assert r.status_code == 409
    assert "недостижим" in r.json()["detail"]  # достижимость проверяется раньше утверждения


def test_run_requires_approval(client):
    plan = _plan(client, {**DEMO1, "horizon_days": 20})  # отдельный план, чтобы не зависеть от порядка тестов
    r = client.post("/api/run", json={"plan_id": plan["plan_id"]})
    assert r.status_code == 409
    assert "Утвердить план" in r.json()["detail"]


def test_approve_is_idempotent_and_run_is_reproducible(client):
    plan = _plan(client, DEMO1)
    pid = plan["plan_id"]
    assert client.post(f"/api/plan/{pid}/approve").json()["approved_version"] == 1
    assert client.post(f"/api/plan/{pid}/approve").json()["approved_version"] == 1

    body = {"plan_id": pid, "scenario_id": "channel_pause", "world_seed": 1}
    r = client.post("/api/run", json=body)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["plan_id"] == pid and run["approved_version"] == 1
    assert run["seeds"] == {"catalog_seed": 0, "world_seed": 1, "noise_seed": 10_001}
    assert len(run["main"]["hours"]) == len(run["frozen"]["hours"]) == 21 * 24
    hour = run["main"]["hours"][100]
    for key in ("requests", "impressions", "unique_reach", "clicks", "conversions", "spend", "ecpm"):
        assert key in hour["by_channel"]["programmatic"], f"в почасовом ряду нет {key}"
    assert hour["status"] in ("ok", "watch", "fire")
    v = run["verdict"]
    assert v["threshold"] == 0.2 and "within_threshold" in v and "frozen_within_threshold" in v
    # тот же запрос → та же кампания: воспроизводимость по зёрнам
    again = client.post("/api/run", json=body).json()
    assert again["verdict"]["actual_kpi"] == run["verdict"]["actual_kpi"]


def test_decide_rejects_hour_without_card(client):
    plan = _approved_plan(client, DEMO1)
    run = client.post("/api/run", json={"plan_id": plan["plan_id"]}).json()
    r = client.post(f"/api/run/{run['run_id']}/decide", json={"hour": 5, "decision": "approve"})
    assert r.status_code == 422
    assert "карточки" in r.json()["detail"]


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"mode": "A", "budget_rub": 1_000_000, "horizon_days": 0}, "срок"),
        ({"mode": "A", "budget_rub": 0}, "бюджет"),
        ({"mode": "B", "target_kpi": "clicks"}, "целевой объём"),
        ({"mode": "A", "preset": "nope", "budget_rub": 1_000_000}, "пресет"),
        ({"mode": "A", "budget_rub": 100_000, "locked": {"sms": 500_000}}, "больше бюджета"),
        ({"mode": "A", "budget_rub": 1_000_000, "channel_ids": []}, "пуст"),
        ({"mode": "A", "budget_rub": 1_000_000, "horizon_days": 90}, "не больше 30"),
    ],
)
def test_bad_brief_is_russian_422(client, body, fragment):
    r = client.post("/api/plan", json=body)
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"]


def test_bad_run_inputs_are_russian_422(client):
    plan = _approved_plan(client, DEMO1)
    pid = plan["plan_id"]
    cases = [
        ({"plan_id": pid, "strategy": "lstm"}, "стратегия"),
        ({"plan_id": pid, "scenario_id": "nope"}, "сценарий"),
        ({"plan_id": pid, "shock": {"channel_id": "sms", "start_hour": 600}}, "за пределами кампании"),
        ({"plan_id": pid, "shock": {"channel_id": "sms", "multiplier": -1}}, "сила шока"),
        ({"plan_id": pid, "shock": {"channel_id": "sms", "parameter": "foo"}}, "параметр шока"),
    ]
    for body, fragment in cases:
        r = client.post("/api/run", json=body)
        assert r.status_code == 422, (body, r.text)
        assert fragment in r.json()["detail"], (body, r.json())


def test_shock_channel_must_belong_to_plan(client):
    plan = _approved_plan(client, {**DEMO1, "preset": "performance"})
    r = client.post("/api/run", json={"plan_id": plan["plan_id"], "shock": {"channel_id": "sms"}})
    assert r.status_code == 422
    assert "нет в плане" in r.json()["detail"]


def test_unknown_plan_is_404_not_500(client):
    r = client.post("/api/run", json={"plan_id": "deadbeef"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_custom_shocks_list_and_legacy_single_field(client):
    plan = _approved_plan(client, DEMO1)
    pid = plan["plan_id"]
    two = [
        {"channel_id": "sms", "parameter": "cvr", "multiplier": 0.5, "start_hour": 0},
        {"channel_id": "programmatic", "parameter": "ecpm", "multiplier": 2, "start_hour": 240},
    ]
    r = client.post("/api/run", json={"plan_id": pid, "shocks": two})
    assert r.status_code == 200, r.text
    run = r.json()
    assert sorted(run["main"]["shock_hours"]) == [0, 240]
    assert [s["channel_id"] for s in run["custom_shocks"]] == ["sms", "programmatic"]
    legacy = client.post("/api/run", json={"plan_id": pid, "shock": {"channel_id": "sms", "start_hour": 24}}).json()
    assert [s["channel_id"] for s in legacy["custom_shocks"]] == ["sms"]
    r = client.post("/api/run", json={"plan_id": pid, "shocks": [{"channel_id": "sms"}] * 4})
    assert r.status_code == 422
    assert "не больше 3" in r.json()["detail"]


def test_empty_plan_cannot_be_approved(client):
    plan = _plan(client, {**DEMO1, "max_cpa_rub": 10})  # потолок ниже любого канала: все бюджеты нулевые
    assert plan["is_empty"] is True and plan["total_budget_rub"] == 0
    r = client.post(f"/api/plan/{plan['plan_id']}/approve")
    assert r.status_code == 409
    assert "пуст" in r.json()["detail"]


def test_suggestion_beyond_cabinet_horizon_is_marked_not_applicable(client):
    plan = _plan(client, {"mode": "B", "preset": "narrow", "target_kpi": "clicks", "target_value": 90_000, "horizon_days": 21})
    sug = {s["changed_field"]: s for s in plan["infeasibility"]["suggestions"]}
    horizon = sug["horizon_days"]
    assert horizon["suggested_value"] > 30
    assert horizon["applicable"] is False and "30" in horizon["why_not"]
    assert all(s["applicable"] for f, s in sug.items() if f != "horizon_days")


def test_infeasible_plan_run_explains_infeasibility_not_approval(client):
    plan = _plan(client, DEMO2)
    r = client.post("/api/run", json={"plan_id": plan["plan_id"]})
    assert r.status_code == 409
    assert "недостижим" in r.json()["detail"]


def test_decide_approve_reruns_and_decline_after_approve_is_rejected(client):
    plan = _approved_plan(client, DEMO1)
    run = client.post("/api/run", json={"plan_id": plan["plan_id"], "scenario_id": "channel_pause"}).json()
    pending = [p["hour"] for p in run["main"]["proposals"] if p["applied_by"] == "pending"]
    assert pending, "в демо-плане с лимитом 50 000 ₽ должна быть карточка, ждущая решения"
    r = client.post(f"/api/run/{run['run_id']}/decide", json={"hour": pending[0], "decision": "approve"})
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["run_id"] != run["run_id"] and after["effect"]["hour"] == pending[0]
    assert any(p["hour"] == pending[0] and p["applied_by"] == "human" for p in after["main"]["proposals"])
    r = client.post(f"/api/run/{after['run_id']}/decide", json={"hour": pending[0], "decision": "decline"})
    assert r.status_code == 422
    # откат своего одобрения: карточка снова ждёт решения, итог возвращается к исходному
    r = client.post(f"/api/run/{after['run_id']}/decide", json={"hour": pending[0], "decision": "undo"})
    assert r.status_code == 200, r.text
    undone = r.json()
    assert undone["effect"]["hour"] == pending[0]
    assert any(p["hour"] == pending[0] and p["applied_by"] == "pending" for p in undone["main"]["proposals"])
    assert undone["verdict"]["actual_kpi"] == run["verdict"]["actual_kpi"]
    r = client.post(f"/api/run/{undone['run_id']}/decide", json={"hour": pending[0], "decision": "undo"})
    assert r.status_code == 422  # отменять уже нечего


def test_static_strategy_twin_equals_main(client):
    plan = _approved_plan(client, DEMO1)
    run = client.post("/api/run", json={"plan_id": plan["plan_id"], "strategy": "static"}).json()
    assert run["frozen"]["actual_kpi"] == run["verdict"]["actual_kpi"]
    assert len(run["frozen"]["hours"]) == len(run["main"]["hours"])

# --------------------------------------------------------------- география и ручная правка


def test_version_matches_pyproject(client):
    """Номер версии в шапке кабинета — тот же, что у пакета: иначе на показе разъедутся."""
    import tomllib
    from pathlib import Path

    from app.main import APP_VERSION

    pyproject = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert client.get("/api/meta").json()["version"] == APP_VERSION == pyproject["project"]["version"]


def test_meta_lists_federal_districts_with_sources(client):
    geo = client.get("/api/meta").json()["geo"]
    assert len(geo["districts"]) == 8
    assert abs(sum(d["weight"] for d in geo["districts"]) - 1.0) < 1e-9
    assert geo["source_url"].startswith("http") and geo["population_as_of"]


def test_narrower_geography_shrinks_capacity_and_forecast(client):
    """Сузили географию — доступной аудитории меньше, значит и результат за те же деньги меньше."""
    whole = _plan(client, DEMO1)
    part = _plan(client, {**DEMO1, "regions": ["cfo", "szfo"]})
    assert part["geo"]["audience_share"] < 0.5
    assert part["total_kpi"] < whole["total_kpi"]
    assert part["plan_id"] != whole["plan_id"]  # разная география — разные планы, а не один из кэша
    assert whole["geo"]["all_russia"] and len(whole["geo"]["split"]) == 8


def test_geography_splits_budget_by_population_and_by_hand(client):
    plan = _plan(client, {**DEMO1, "regions": ["cfo", "szfo"]})
    by_population = {row["id"]: row["budget_rub"] for row in plan["geo"]["split"]}
    assert by_population["cfo"] > by_population["szfo"]  # в ЦФО людей больше
    assert abs(sum(by_population.values()) - plan["total_budget_rub"]) < 1.0

    manual = _plan(client, {**DEMO1, "regions": ["cfo", "szfo"], "region_split": {"cfo": 50, "szfo": 50}})
    halves = {row["id"]: row["budget_rub"] for row in manual["geo"]["split"]}
    assert manual["geo"]["manual_split"]
    assert abs(halves["cfo"] - halves["szfo"]) < 1.0


def test_unknown_region_is_russian_422(client):
    r = client.post("/api/plan", json={**DEMO1, "regions": ["cfo", "mars"]})
    assert r.status_code == 422
    assert "mars" in r.json()["detail"] and "округа" in r.json()["detail"]


def test_locked_channel_holds_its_budget_and_changes_the_rest(client):
    """Ручной бегунок: канал зафиксирован, остальной бюджет планировщик раскладывает заново."""
    base = _plan(client, DEMO1)
    victim = min(base["allocations"], key=lambda a: a["budget_rub"])
    want = base["total_budget_rub"] * 0.2
    fixed = _plan(client, {**DEMO1, "locked": {victim["channel_id"]: want}})
    got = next(a for a in fixed["allocations"] if a["channel_id"] == victim["channel_id"])
    assert got["locked"] is True
    assert got["budget_rub"] > victim["budget_rub"]  # канал получил больше, чем дал расчёт
    assert min(got["budget_rub"], want) == pytest.approx(got["budget_rub"], rel=0.01)  # либо просили, либо упёрлись в ёмкость
    assert fixed["total_kpi"] <= base["total_kpi"] * 1.0001  # расчёт не бывает хуже ручной правки
    assert abs(sum(a["budget_rub"] for a in fixed["allocations"]) - fixed["total_budget_rub"]) < 1.0


def test_locked_more_than_budget_is_russian_422(client):
    r = client.post("/api/plan", json={**DEMO1, "locked": {"social_1": 2_000_000}})
    assert r.status_code == 422
    assert "больше бюджета" in r.json()["detail"]


def test_run_uses_the_same_world_as_the_plan(client):
    """Прогон идёт по тому же каталогу, что и план: иначе факт разошёлся бы с планом на ровном месте."""
    plan = _approved_plan(client, {**DEMO1, "regions": ["cfo"]})
    r = client.post("/api/run", json={"plan_id": plan["plan_id"], "scenario_id": "stable"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert abs(run["verdict"]["final_deviation_kpi"]) < 0.35  # в узкой географии план и факт всё ещё об одном мире

def test_meta_exposes_targeting_axes(client):
    """Кабинету нужны доли осей сегмента: он показывает цену выбора и не выдумывает числа сам."""
    axes = client.get("/api/meta").json()["targeting"]
    for axis in ("age_groups", "genders", "geo"):
        assert abs(sum(axes[axis].values()) - 1.0) < 1e-6, axis
    assert set(axes["geo"]) == set(axes["geo_price"])


def test_targeting_and_geography_narrow_the_plan_together(client):
    """Сегмент режет аудиторию по людям, география — по стране; вместе — сильнее, чем каждый по себе."""
    whole = _plan(client, DEMO1)
    segment = _plan(client, {**DEMO1, "targeting": {"age_groups": ["25_34"], "geo": ["large_cities"]}})
    both = _plan(client, {**DEMO1, "targeting": {"age_groups": ["25_34"], "geo": ["large_cities"]}, "regions": ["cfo", "szfo"]})
    assert segment["total_kpi"] < whole["total_kpi"]
    assert both["total_kpi"] < segment["total_kpi"]
    assert len({whole["plan_id"], segment["plan_id"], both["plan_id"]}) == 3
    assert both["geo"]["audience_share"] < 0.5  # география в ответе остаётся про округа, а не про сегмент


def test_geography_registry_knows_cities_for_impossible_combinations(client):
    """«Столицы» без Центрального и Северо-Западного округов — пустое условие, кабинет обязан это видеть."""
    districts = {d["id"]: d for d in client.get("/api/meta").json()["geo"]["districts"]}
    assert districts["cfo"]["federal_cities"] == ["Москва"]
    assert districts["szfo"]["federal_cities"] == ["Санкт-Петербург"]
    assert all(not districts[d]["federal_cities"] for d in ("yufo", "skfo", "pfo", "ufo", "sfo", "dfo"))
    assert not districts["dfo"]["million_cities"] and not districts["skfo"]["million_cities"]
    assert len(sum((d["million_cities"] for d in districts.values()), [])) == 16

# ------------------------------------------------- итог по плану и ряды по периодам


def test_plan_totals_aggregate_the_whole_media_plan(client):
    """Постановка требует те же метрики, что по каналам, и по медиаплану целиком."""
    plan = _plan(client, DEMO1)
    t = plan["totals"]
    allocations = plan["allocations"]
    assert t["budget_rub"] == pytest.approx(plan["total_budget_rub"])
    for field in ("impressions", "clicks", "conversions", "unique_reach"):
        assert t[field] == pytest.approx(sum(a[field] for a in allocations))
    # качество и цены — от объёмов, а не среднее арифметическое долей по каналам
    assert t["ctr"] == pytest.approx(t["clicks"] / t["impressions"])
    assert t["cvr"] == pytest.approx(t["conversions"] / t["clicks"])
    assert t["cpm_rub"] == pytest.approx(t["budget_rub"] / t["impressions"] * 1000)
    assert t["cpc_rub"] == pytest.approx(t["budget_rub"] / t["clicks"])
    assert t["cpa_rub"] == pytest.approx(t["budget_rub"] / t["conversions"])
    naive_ctr = sum(a["ctr"] for a in allocations) / len(allocations)
    assert t["ctr"] != pytest.approx(naive_ctr)  # взвешивание по объёму, иначе мелкий канал перетянет


def test_plan_totals_report_video_share_behind_vtr(client):
    """VTR есть не у всех каналов, поэтому вместе с ним говорим, какая доля показов учтена."""
    t = _plan(client, DEMO1)["totals"]
    assert 0 < t["vtr_impressions_share"] < 1
    assert 0 < t["vtr"] <= 1


def test_plan_series_by_days_and_weeks(client):
    plan = _plan(client, DEMO1)
    days, weeks = plan["series"]["days"], plan["series"]["weeks"]
    assert len(days) == DEMO1["horizon_days"]
    assert len(weeks) == 3  # 21 день это три полные недели
    assert [w["days"] for w in weeks] == [[1, 7], [8, 14], [15, 21]]
    assert sum(d["budget_rub"] for d in days) == pytest.approx(plan["total_budget_rub"], rel=1e-6)
    assert sum(w["budget_rub"] for w in weeks) == pytest.approx(plan["total_budget_rub"], rel=1e-6)
    assert days[-1]["cum_kpi"] == pytest.approx(weeks[-1]["cum_kpi"])
    assert all(a["cum_kpi"] <= b["cum_kpi"] for a, b in zip(days, days[1:], strict=False))  # накопление не убывает


def test_zero_and_negative_budget_are_rejected_with_their_own_message(client):
    """Раньше на отрицательный бюджет отвечала проверка фиксаций каналов, и человек читал не про то."""
    for budget in (0, -5000):
        r = client.post("/api/plan", json={**DEMO1, "budget_rub": budget})
        assert r.status_code == 422
        assert "бюджет должен быть больше нуля" in r.json()["detail"]
    r = client.post("/api/plan", json={**DEMO2, "target_value": -10})
    assert r.status_code == 422
    assert "целевой объём должен быть больше нуля" in r.json()["detail"]


def test_plan_view_exposes_why_budget_is_not_placed(client):
    """Кабинету нужны строки о недоразмещении отдельно от общего объяснения."""
    data = _plan(client, {**DEMO1, "max_cpa_rub": 100})
    assert data["total_budget_rub"] < 1_200_000
    assert data["shortfall"], "кабинету нечего показать про недоразмещение"
    assert any("потолок средней цены" in line for line in data["shortfall"])
