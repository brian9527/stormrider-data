import errno
import subprocess
from unittest.mock import Mock

import pytest

import routing.build as rbuild
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
        return subprocess.CompletedProcess(cmd, 0, stderr="")
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


@pytest.mark.parametrize("command", [build_command, serve_command])
def test_docker_mount_uses_an_absolute_host_path_with_spaces(command):
    # RUNNER_TEMP is on the host. Keep the mount as one argv element, without shell quoting.
    graph = rbuild.Path(".build/graph folder")
    cmd = command(graph, 8002) if command is serve_command else command(graph)
    assert cmd[cmd.index("-v") + 1] == f"{graph.resolve()}:/custom_files"


def test_container_names_follow_the_build_folder(tmp_path):
    # A container from an older build does not reserve the next build's name.
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
    assert (graph / pbf.name).samefile(pbf)
    with pytest.raises(FileExistsError):
        build_graph(pbf, graph, run=run)


def test_build_graph_fails_without_a_tar(tmp_path):
    pbf = tmp_path / "taiwan.osm.pbf"
    pbf.write_bytes(b"pbf")
    with pytest.raises(BuildFailed):
        build_graph(pbf, tmp_path / "graph", run=_builder(tmp_path / "graph", [], tar=False))


@pytest.mark.parametrize("error", [KeyboardInterrupt(), subprocess.CalledProcessError(125, ["docker", "run"])])
def test_a_failed_or_interrupted_build_removes_its_container(tmp_path, error):
    pbf = tmp_path / "taiwan.osm.pbf"
    pbf.write_bytes(b"pbf")
    graph = tmp_path / "graph-20261101T183000Z"
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise error  # Ctrl-C or a container created before docker run fails
        assert kw["timeout"] == 60
        return subprocess.CompletedProcess(cmd, 0, stderr="")
    with pytest.raises(type(error)) as caught:
        build_graph(pbf, graph, run=run)
    assert caught.value is error
    assert calls[-1] == ["docker", "rm", "-f", container_name(BUILD_CONTAINER, graph)]


def test_serving_always_stops_the_container(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stderr="")

    with pytest.raises(RuntimeError):
        with serving(tmp_path, 8002, run=run) as url:
            assert url == "http://127.0.0.1:8002"
            raise RuntimeError("check crashed")
    assert calls[-1] == ["docker", "rm", "-f", container_name(CHECK_CONTAINER, tmp_path)]


def test_serving_removes_the_container_when_starting_it_fails(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.CalledProcessError(125, cmd)  # e.g. the port is taken
        return subprocess.CompletedProcess(cmd, 0, stderr="")
    with pytest.raises(subprocess.CalledProcessError):
        with serving(tmp_path, 8002, run=run):
            pass
    assert calls[-1] == ["docker", "rm", "-f", container_name(CHECK_CONTAINER, tmp_path)]


@pytest.mark.parametrize("failure", ["timeout", "oserror", "nonzero"])
@pytest.mark.parametrize("original", [None, RuntimeError("check crashed"), KeyboardInterrupt()])
def test_cleanup_failures_warn_without_changing_the_run_result(tmp_path, capsys, failure, original):
    def run(cmd, **kw):
        if cmd[:2] == ["docker", "rm"]:
            assert kw["timeout"] == 60 and kw["check"] is False
            if failure == "timeout":
                raise subprocess.TimeoutExpired(cmd, kw["timeout"])
            if failure == "oserror":
                raise OSError("Docker executable unavailable")
            return subprocess.CompletedProcess(cmd, 1, stderr="Cannot connect to the Docker daemon")
        return subprocess.CompletedProcess(cmd, 0, stderr="")

    def check():
        with serving(tmp_path, 8002, run=run):
            if original is not None:
                raise original

    if original is None:
        check()
    else:
        with pytest.raises(type(original)) as caught:
            check()
        assert caught.value is original
    warning = capsys.readouterr().out
    assert "warning: could not remove container" in warning
    assert container_name(CHECK_CONTAINER, tmp_path) in warning


def test_cleanup_is_quiet_when_docker_already_auto_removed_the_container(tmp_path, capsys):
    def run(cmd, **kw):
        if cmd[:2] == ["docker", "rm"]:
            return subprocess.CompletedProcess(
                cmd, 1, stderr=f"Error response from daemon: No such container: {cmd[-1]}\n")
        return subprocess.CompletedProcess(cmd, 0, stderr="")

    with serving(tmp_path, run=run):
        pass
    assert capsys.readouterr().out == ""


def test_build_falls_back_when_the_extract_is_on_another_filesystem(tmp_path, monkeypatch):
    pbf = tmp_path / "taiwan.osm.pbf"
    pbf.write_bytes(b"pbf")
    graph = tmp_path / "graph"
    link = Mock(side_effect=OSError(errno.EXDEV, "cross-device link"))
    copy = Mock()
    monkeypatch.setattr(rbuild.os, "link", link)
    monkeypatch.setattr(rbuild.shutil, "copyfile", copy)
    build_graph(pbf, graph, run=_builder(graph, []))
    link.assert_called_once_with(pbf, graph / pbf.name)
    copy.assert_called_once_with(pbf, graph / pbf.name)
    assert pbf.read_bytes() == b"pbf"
