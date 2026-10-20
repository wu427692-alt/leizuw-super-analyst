"""Offline YAML contracts for the Linux-volume overlay; no Docker/app/secret reads.

These assert the tagged overlay's scope, not a reimplementation of Compose merge.
Actual CLI merging, missing-volume rejection and UID 1000 filesystem acceptance
must also be checked with the target Compose/runtime before business migration.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = REPO_ROOT / "docker" / "docker-compose.macmini.yml"
OVERLAY_PATH = REPO_ROOT / "docker" / "docker-compose.macmini-volumes.yml"
ENV_EXAMPLE_PATH = REPO_ROOT / "docker" / "macmini.env.example"


class OverrideSequence(list):
    """Retain !override explicitly instead of silently ignoring its semantics."""


class OverlayLoader(yaml.SafeLoader):
    pass


def _construct_override(loader: OverlayLoader, node: yaml.SequenceNode) -> OverrideSequence:
    if not isinstance(node, yaml.SequenceNode):
        raise yaml.constructor.ConstructorError(
            None, None, "This overlay requires !override on a mount sequence", node.start_mark,
        )
    return OverrideSequence(loader.construct_sequence(node, deep=True))


OverlayLoader.add_constructor("!override", _construct_override)
BASE = yaml.safe_load(BASE_PATH.read_text(encoding="utf-8"))
OVERLAY = yaml.load(OVERLAY_PATH.read_text(encoding="utf-8"), Loader=OverlayLoader)
EXPECTED_TARGETS = {
    "data": "/app/data",
    "logs": "/app/logs",
    "reports": "/app/reports",
    "strategies": "/app/strategies",
    "config": "/app/config",
    "tyc": "/home/dsa/.tyc",
    "longbridge_tokens": "/home/dsa/.longbridge",
}


def test_overlay_only_replaces_existing_service_mounts() -> None:
    # No new service/init job, command, environment, entrypoint, profile, network,
    # port, image or privilege override can bypass the base security contracts.
    assert set(OVERLAY) == {"services", "volumes"}
    assert set(OVERLAY["services"]) == set(BASE["services"]) == {"server", "monitor-worker"}
    for service in OVERLAY["services"].values():
        assert set(service) == {"volumes"}


@pytest.mark.parametrize("service_name", ["server", "monitor-worker"])
def test_each_mount_sequence_has_explicit_override_tag(service_name: str) -> None:
    mounts = OVERLAY["services"][service_name]["volumes"]
    assert isinstance(mounts, OverrideSequence), "Normal merge can retain bind metadata"
    assert len(mounts) == len(EXPECTED_TARGETS)


def test_server_and_collector_share_the_same_working_volume_set() -> None:
    server_mounts = OVERLAY["services"]["server"]["volumes"]
    worker_mounts = OVERLAY["services"]["monitor-worker"]["volumes"]
    assert server_mounts is worker_mounts  # Tagged YAML anchor/alias, not two drifting lists.


@pytest.mark.parametrize("service_name", ["server", "monitor-worker"])
def test_mounts_are_explicit_linux_volumes_without_implicit_copy_or_host_paths(service_name: str) -> None:
    mounts = OVERLAY["services"][service_name]["volumes"]
    assert {mount["source"] for mount in mounts} == set(EXPECTED_TARGETS)
    assert {mount["target"] for mount in mounts} == set(EXPECTED_TARGETS.values())
    assert set(OVERLAY["volumes"]) == set(EXPECTED_TARGETS)
    for mount in mounts:
        assert set(mount) <= {"type", "source", "target", "read_only", "volume"}
        assert mount["type"] == "volume"
        assert mount["target"] == EXPECTED_TARGETS[mount["source"]]
        assert mount["volume"] == {"nocopy": True}
        assert "bind" not in mount


@pytest.mark.parametrize("service_name", ["server", "monitor-worker"])
def test_private_mount_read_only_controls_match_the_base(service_name: str) -> None:
    originals = {mount["target"]: mount for mount in BASE["services"][service_name]["volumes"]}
    mounts = OVERLAY["services"][service_name]["volumes"]
    assert set(originals) == set(EXPECTED_TARGETS.values())
    for mount in mounts:
        assert mount.get("read_only", False) == originals[mount["target"]].get("read_only", False)
    by_name = {mount["source"]: mount for mount in mounts}
    assert by_name["strategies"]["read_only"] is True
    assert by_name["config"]["read_only"] == "${DSA_MACMINI_CONFIG_READ_ONLY:-true}"
    assert by_name["tyc"]["read_only"] == "${DSA_MACMINI_TYC_READ_ONLY:-true}"


@pytest.mark.parametrize("volume_name", list(EXPECTED_TARGETS))
def test_volumes_are_preexisting_and_require_an_explicit_nonempty_prefix(volume_name: str) -> None:
    definition = OVERLAY["volumes"][volume_name]
    # No driver_opts bind/NFS/tmpfs, auto-created volume or hardcoded shared name.
    assert set(definition) == {"external", "name"}
    assert definition["external"] is True
    assert re.fullmatch(
        r"\$\{DSA_MACMINI_VOLUME_PREFIX:\?[^{}]+\}_" + re.escape(volume_name),
        definition["name"],
    )


def test_seven_volume_names_are_distinct() -> None:
    assert len({definition["name"] for definition in OVERLAY["volumes"].values()}) == 7


def test_volume_prefix_is_documented_but_not_preselected() -> None:
    # Read only the public deployment example, never an actual .env.
    # Keep the optional overlay parameter commented in the base-only example.
    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    declarations = re.findall(r"^# DSA_MACMINI_VOLUME_PREFIX=(.*)$", text, re.MULTILINE)
    assert declarations == [""]
    assert not re.search(r"^DSA_MACMINI_VOLUME_PREFIX=", text, re.MULTILINE)
    assert "docker-compose.macmini-volumes.yml" in text
    assert "DSA_MACMINI_RUNTIME_DIR" in text
