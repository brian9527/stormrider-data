import pytest

import subprocess

from routing.build import (BUILD_CONTAINER, CHECK_CONTAINER, TAR_NAME, VALHALLA_IMAGE, BuildFailed, build_command,
                           build_graph, container_name, serve_command, serving)


def _builder(graph, calls, tar=True, config=True):
    """A fake subprocess.run: `docker run` leaves what the image would; every call is recorded."""
    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            if tar:
                (graph / TAR_NAME).write_bytes(b"tiles")
            if config:
                (graph / "valhalla.json").write_text("{}")
    return run


def test_build_graph_fails_without_the_generated_config(tmp_path):
    pbf = tmp_path / "taiwan.osm.pbf"
    pbf.write_bytes(b"pbf")
    graph = tmp_path / "graph"
    with pytest.raises(BuildFailed):
        build_graph(pbf, graph, run=_builder(graph, [], config=False))


def test_build_runs_offline_without_admins_time_zones_or_speed_downloads(tmp_path):
    cmd = build_command(tmp_path)
    assert cmd[:6] == ["docker", "run", "--rm", "--name", container_name(BUILD_CONTAINER, tmp_path), "--network"]
    assert cmd[6] == "none" and cmd[-1] == VALHALLA_IMAGE
    env = {cmd[i + 1] for i, a in enumerate(cmd) if a == "-e"}
    assert {"build_admins=False", "build_time_zones=False", "use_default_speeds_config=False",
            "serve_tiles=False", "build_tar=True", "force_rebuild=True"} <= env
    assert "@sha256:" in VALHALLA_IMAGE


def test_serve_binds_to_localhost_only(tmp_path):
    cmd = serve_command(tmp_path, 8002)
    assert ["-p", "127.0.0.1:8002:8002"] == cmd[cmd.index("-p"):cmd.index("-p") + 2]
    assert container_name(CHECK_CONTAINER, tmp_path) in cmd and "serve_tiles=True" in cmd


def test_container_names_follow_the_build_folder(tmp_path):
    # A container left by a killed run never blocks the next one, whose folder is new.
    a, b = tmp_path / "graph-20261101T183000Z", tmp_path / "graph-20261201T183000Z"
    assert container_name(BUILD_CONTAINER, a) == "sr-valhalla-build-graph-20261101T183000Z"
    assert container_name(BUILD_CONTAINER, a) != container_name(BUILD_CONTAINER, b)
    assert container_name(CHECK_CONTAINER, a) != container_name(BUILD_CONTAINER, a)


def test_build_graph_needs_a_fresh_folder_and_returns_the_tar(tmp_path):
    pbf = tmp_path / "taiwan.osm.pbf"
    pbf.write_bytes(b"pbf")
    graph = tmp_path / "graph"
    run = _builder(graph, [])
    assert build_graph(pbf, graph, run=run) == graph / TAR_NAME
    assert (graph / "taiwan.osm.pbf").read_bytes() == b"pbf"
    with pytest.raises(FileExistsError):
        build_graph(pbf, graph, run=run)


def test_build_graph_fails_without_a_tar(tmp_path):
    pbf = tmp_path / "taiwan.osm.pbf"
    pbf.write_bytes(b"pbf")
    with pytest.raises(BuildFailed):
        build_graph(pbf, tmp_path / "graph", run=_builder(tmp_path / "graph", [], tar=False))


def test_a_failed_or_interrupted_build_removes_its_container(tmp_path):
    pbf = tmp_path / "taiwan.osm.pbf"
    pbf.write_bytes(b"pbf")
    graph = tmp_path / "graph-20261101T183000Z"
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise KeyboardInterrupt  # Ctrl-C while the build runs
    with pytest.raises(KeyboardInterrupt):
        build_graph(pbf, graph, run=run)
    assert calls[-1] == ["docker", "rm", "-f", container_name(BUILD_CONTAINER, graph)]


def test_serving_always_stops_the_container(tmp_path):
    calls = []
    with pytest.raises(RuntimeError):
        with serving(tmp_path, 8002, run=lambda cmd, **kw: calls.append(cmd)) as url:
            assert url == "http://127.0.0.1:8002"
            raise RuntimeError("check crashed")
    assert calls[-1] == ["docker", "rm", "-f", container_name(CHECK_CONTAINER, tmp_path)]


def test_serving_removes_the_container_when_starting_it_fails(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.CalledProcessError(125, cmd)  # e.g. the port is taken
    with pytest.raises(subprocess.CalledProcessError):
        with serving(tmp_path, 8002, run=run):
            pass
    assert calls[-1] == ["docker", "rm", "-f", container_name(CHECK_CONTAINER, tmp_path)]


def test_a_hanging_cleanup_does_not_hide_the_real_error(tmp_path):
    def run(cmd, **kw):
        if cmd[:2] == ["docker", "rm"]:
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    with pytest.raises(RuntimeError, match="check crashed"):
        with serving(tmp_path, 8002, run=run):
            raise RuntimeError("check crashed")
