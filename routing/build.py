"""Build the routing graph with the pinned Valhalla image, and serve it briefly for the checks
(route C spec §3.2)."""
from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

# Valhalla 3.9.1, valhalla-scripted multi-arch index digest (amd64 image built 2026-10-06). The
# server runs the same digest (stormrider-server compose.yaml); upgrading means a new graph.
VALHALLA_IMAGE = ("ghcr.io/valhalla/valhalla-scripted@sha256:"
                  "6238809f9dc3057743d70a20efd3911ec38e458bfa13a0601909b58f68dfd956")
# Name prefixes; each run adds its own graph folder name (container_name).
BUILD_CONTAINER = "sr-valhalla-build"
CHECK_CONTAINER = "sr-valhalla-check"
# Seconds a `docker rm -f` may take before cleanup gives up and lets the run's own error through.
CLEANUP_TIMEOUT_S = 60
TAR_NAME = "valhalla_tiles.tar"
# The configuration the image generated for the build; its hash goes into the manifest (spec §3.4).
CONFIG_NAME = "valhalla.json"
# No administrative or time-zone databases: Taiwan's border relation is incomplete in the Geofabrik
# extract and Valhalla drops it anyway, so the default rules apply either way (10/09 RESULTS §3).
# No elevation, no transit, and no download of speed data, so the build needs no network.
COMMON_ENV = {
    "build_elevation": "False",
    "build_time_zones": "False",
    "build_admins": "False",
    "build_transit": "False",
    "use_default_speeds_config": "False",
}
BUILD_ENV = {**COMMON_ENV, "force_rebuild": "True", "serve_tiles": "False", "build_tar": "True",
             "server_threads": "4"}
SERVE_ENV = {**COMMON_ENV, "use_tiles_ignore_pbf": "True", "force_rebuild": "False",
             "update_existing_config": "False", "serve_tiles": "True"}


class BuildFailed(Exception):
    pass


def _env_args(env: dict) -> list[str]:
    args = []
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    return args


def container_name(prefix: str, graph_dir: Path) -> str:
    """One name per run: graph folders are named after the build id, so a container left behind by a
    killed run never blocks the next one."""
    return f"{prefix}-{graph_dir.name}"


def _remove_container(name: str, run) -> None:
    """Stops and removes a container; harmless when `--rm` already did. A hanging Docker must not
    replace the error that brought us here."""
    try:
        run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=CLEANUP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(f"warning: could not remove container {name} in {CLEANUP_TIMEOUT_S} s", flush=True)


def build_command(graph_dir: Path) -> list[str]:
    return ["docker", "run", "--rm", "--name", container_name(BUILD_CONTAINER, graph_dir), "--network", "none",
            "-v", f"{graph_dir.resolve()}:/custom_files", *_env_args(BUILD_ENV), VALHALLA_IMAGE]


def serve_command(graph_dir: Path, port: int) -> list[str]:
    return ["docker", "run", "-d", "--rm", "--name", container_name(CHECK_CONTAINER, graph_dir),
            "-p", f"127.0.0.1:{port}:8002",
            "-v", f"{graph_dir.resolve()}:/custom_files", *_env_args(SERVE_ENV), VALHALLA_IMAGE]


def build_graph(pbf: Path, graph_dir: Path, run=subprocess.run) -> Path:
    """Builds into a folder that must not exist yet (no leftovers from an earlier run)."""
    graph_dir.mkdir(parents=True, exist_ok=False)
    target = graph_dir / pbf.name
    try:
        os.link(pbf, target)
    except OSError:
        shutil.copyfile(pbf, target)
    try:
        run(build_command(graph_dir), check=True)
    finally:
        # A build cut short (Ctrl-C, a failed step) must not go on in the background.
        _remove_container(container_name(BUILD_CONTAINER, graph_dir), run)
    tar = graph_dir / TAR_NAME
    if not tar.is_file() or tar.stat().st_size == 0:
        raise BuildFailed(f"the build left no {TAR_NAME}")
    if not (graph_dir / CONFIG_NAME).is_file():
        raise BuildFailed(f"the build left no {CONFIG_NAME}")
    return tar


@contextmanager
def serving(graph_dir: Path, port: int = 8002, run=subprocess.run):
    try:
        # Inside the try: a start that fails half way can still leave a container behind.
        run(serve_command(graph_dir, port), check=True, stdout=subprocess.DEVNULL)
        yield f"http://127.0.0.1:{port}"
    finally:
        _remove_container(container_name(CHECK_CONTAINER, graph_dir), run)
