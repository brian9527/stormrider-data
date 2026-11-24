import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "map-styles.yml"
SEARCH = WORKFLOW.with_name("search-data.yml")


def text():
    return WORKFLOW.read_text(encoding="utf-8")


def test_runs_monthly_and_on_demand_on_main_only():
    t = text()
    assert re.search(r'cron: "0 19 1 \* \*"', t)
    assert "workflow_dispatch:" in t
    assert "if: github.ref == 'refs/heads/main'" in t
    # A trigger, not the word in a comment: the comment explains why there is none.
    assert not re.search(r"^\s*pull_request\s*:", t, re.M)


def test_the_github_token_can_only_read_the_repo():
    t = text()
    assert "\npermissions:\n  contents: read\n\n" in t
    assert t.count("permissions:") == 1


def test_one_publisher_at_a_time():
    assert re.search(r"concurrency:\n\s+group: update-map-styles\n\s+cancel-in-progress: false", text())


def test_pins_the_same_actions_as_the_search_workflow():
    pins = set(re.findall(r"uses: (\S+@[0-9a-f]{40})", SEARCH.read_text(encoding="utf-8")))
    used = set(re.findall(r"uses: (\S+)", text()))
    assert used and used <= pins


def test_installs_only_the_weather_requirements_with_hashes():
    t = text()
    assert "pip install --require-hashes --only-binary=:all: -r requirements.txt" in t
    assert "requirements-search.txt" not in t


def test_tests_then_publishes_to_the_weather_bucket_with_the_r2_secrets():
    t = text()
    assert t.index("pytest tests/mapstyle") < t.index("python -m mapstyle.main")
    assert "R2_BUCKET: stormrider-weather" in t
    for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        assert f"{name}: ${{{{ secrets.{name} }}}}" in t


def test_the_weather_workflow_keeps_this_schedule_alive():
    # GitHub may stop schedules after 60 days without repo activity, with no failed run to notice
    # (Codex final review, data 7). Its failure must not fail the weather run, like search-data.yml.
    update = (WORKFLOW.parent / "update.yml").read_text(encoding="utf-8")
    step = update.split("actions/workflows/map-styles.yml/enable", 1)
    assert len(step) == 2
    assert step[1].lstrip('"').lstrip().startswith("|| echo \"::warning::could not re-enable map-styles.yml")
