"""Offline guards for the workflow's failure isolation and shared input contract.

These inspect the current step syntax, not GitHub's expression evaluator or runner lifecycle.
"""
from pathlib import Path
import re


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/search-data.yml"


def _workflow():
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, re.split(r"^      - ", text, flags=re.M)[1:]


def _step(steps, marker):
    matches = [step for step in steps if marker in step]
    assert len(matches) == 1, f"expected one step containing {marker!r}"
    return matches[0]


def test_search_and_routing_share_one_download_record():
    _, steps = _workflow()
    download = _step(steps, "python -m search.sources")
    search = _step(steps, "python -m search.main")
    routing = _step(steps, "python -m routing.main")
    assert download.startswith("id: pbf\n")
    assert '--pbf-out "$RUNNER_TEMP/osm/taiwan-latest.osm.pbf"' in download
    assert '--record "$RUNNER_TEMP/osm/pbf.json"' in download
    for step in (search, routing):
        assert '--pbf-file "$RUNNER_TEMP/osm/taiwan-latest.osm.pbf"' in step
        assert '--pbf-record "$RUNNER_TEMP/osm/pbf.json"' in step
    assert steps.index(download) < steps.index(search) < steps.index(routing)


def test_search_failure_does_not_gate_routing_or_get_hidden():
    text, steps = _workflow()
    search = _step(steps, "python -m search.main")
    cleanup = _step(steps, 'rm -rf "$RUNNER_TEMP/search"')
    routing = _step(steps, "python -m routing.main")
    # cancelled() is a status function: this avoids the implicit success() guard after failures.
    for step in (cleanup, routing):
        assert step.splitlines()[0] == "if: ${{ !cancelled() && steps.pbf.outcome == 'success' }}"
    assert "continue-on-error:" not in text
    assert "|| true" not in search and "|| true" not in routing
    assert re.search(r"^    timeout-minutes: 150$", text, re.M)
    assert re.search(r"^        timeout-minutes: 100$", search, re.M)


def test_meilisearch_is_stopped_before_routing_even_after_search_failure():
    _, steps = _workflow()
    search = _step(steps, "python -m search.main")
    cleanup = _step(steps, 'rm -rf "$RUNNER_TEMP/search"')
    routing = _step(steps, "python -m routing.main")
    assert "MEILI_CONTAINER_ID: ${{ job.services.meilisearch.id }}" in cleanup
    assert 'docker stop --time 10 "$MEILI_CONTAINER_ID"' in cleanup
    assert re.search(r"^        timeout-minutes: 2$", cleanup, re.M)
    assert cleanup.index("docker stop") < cleanup.index("free -m")
    assert steps.index(search) < steps.index(cleanup) < steps.index(routing)
