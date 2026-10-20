"""Offline release guards: fake Docker, real shell and temporary static trees."""
import os
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/publish-nginx-assets.sh"


def make_bundle(root, version):
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        f'<script type="module" src="/assets/index-{version}.js"></script>'
        f'<link rel="stylesheet" href="/assets/index-{version}.css">', encoding="utf-8",
    )
    (root / "assets" / f"index-{version}.js").write_text(f"// {version}", encoding="utf-8")
    (root / "assets" / f"index-{version}.css").write_text(f"/* {version} */", encoding="utf-8")


@pytest.fixture
def release_layout(tmp_path):
    source = tmp_path / "container"
    releases = tmp_path / "releases"
    previous = releases / "approved"
    active = tmp_path / "active"
    make_bundle(source, "approved")
    make_bundle(previous, "approved")
    active.symlink_to(previous, target_is_directory=True)
    return source, releases, previous, active


def run_script(layout, *args, running="true"):
    source, releases, _, active = layout
    wrapper = r'''
docker() {
  case "$1" in
    inspect) printf '%s\n' "$FAKE_CONTAINER_RUNNING" ;;
    cp) cp -a "$FAKE_CONTAINER_STATIC/." "$3" ;;
    *) return 1 ;;
  esac
}
export -f docker
bash "$RELEASE_SCRIPT" "$@"
'''
    env = {
        **os.environ,
        "RELEASE_SCRIPT": str(SCRIPT),
        "FAKE_CONTAINER_STATIC": str(source),
        "FAKE_CONTAINER_RUNNING": running,
        "LEZIWU_CONTAINER_NAME": "offline-test-container",
        "LEZIWU_STATIC_RELEASE_ROOT": str(releases),
        "LEZIWU_STATIC_ROOT": str(active),
    }
    return subprocess.run(
        ["bash", "-c", wrapper, "offline-test", *args], env=env,
        capture_output=True, text=True, timeout=10,
    )


def assert_unchanged(layout):
    _, releases, previous, active = layout
    assert active.resolve() == previous
    assert sorted(path.name for path in releases.iterdir()) == ["approved"]


def test_check_is_read_only_and_compares_sha_and_entries(release_layout):
    _, _, _, active = release_layout
    link_mtime = active.lstat().st_mtime_ns
    result = run_script(release_layout, "--check")
    assert result.returncode == 0, result.stderr
    assert "Container index SHA256=" in result.stdout
    assert "Published index SHA256=" in result.stdout
    assert "index-approved.js" in result.stdout
    assert active.lstat().st_mtime_ns == link_mtime
    assert_unchanged(release_layout)


@pytest.mark.parametrize("args", [(), ("--check",)])
def test_stale_container_cannot_overwrite_published_frontend(release_layout, args):
    source, _, _, _ = release_layout
    # Both bundles remain complete; only their entry document differs.
    with (source / "index.html").open("a", encoding="utf-8") as handle:
        handle.write("<!-- old backend image -->")
    result = run_script(release_layout, *args)
    assert result.returncode != 0
    assert "Frontend entry mismatch" in result.stderr
    assert_unchanged(release_layout)


@pytest.mark.parametrize("side", [0, 2])
@pytest.mark.parametrize("missing", ["index.html", "assets/index-approved.js"])
def test_check_rejects_missing_html_or_entry_assets(release_layout, side, missing):
    (release_layout[side] / missing).unlink()
    result = run_script(release_layout, "--check")
    assert result.returncode != 0
    assert_unchanged(release_layout)


def test_check_rejects_dangling_release_link(release_layout):
    _, _, previous, active = release_layout
    active.unlink()
    active.symlink_to(previous.parent / "absent", target_is_directory=True)
    result = run_script(release_layout, "--check")
    assert result.returncode != 0
    assert active.is_symlink() and not active.exists()


def test_check_does_not_create_missing_release_root(release_layout, tmp_path):
    source, _, _, _ = release_layout
    absent_root = tmp_path / "not-published"
    layout = source, absent_root, absent_root / "old", tmp_path / "absent-active"
    result = run_script(layout, "--check")
    assert result.returncode != 0
    assert not absent_root.exists()
    assert not layout[3].exists()


@pytest.mark.parametrize("malformation", ["no_refs", "missing_js", "missing_css"])
def test_explicit_release_still_requires_complete_entry_assets(release_layout, malformation):
    source, _, _, _ = release_layout
    if malformation == "no_refs":
        (source / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    else:
        suffix = "js" if malformation == "missing_js" else "css"
        (source / "assets" / f"index-approved.{suffix}").unlink()
    result = run_script(release_layout, "--publish-new-version")
    assert result.returncode != 0
    assert_unchanged(release_layout)


def test_explicit_publish_retains_old_lazy_chunks_and_previous_release(release_layout):
    source, releases, previous, active = release_layout
    (source / "index.html").write_text(
        '<script src="/assets/new-entry.js"></script><link href="/assets/new-entry.css">',
        encoding="utf-8",
    )
    (source / "assets/new-entry.js").write_text("// new", encoding="utf-8")
    (source / "assets/new-entry.css").write_text("/* new */", encoding="utf-8")
    (previous / "assets/old-lazy-chunk.js").write_text("// old lazy", encoding="utf-8")
    (previous / "assets/old-font.woff2").write_bytes(b"old-font")
    previous_index = (previous / "index.html").read_bytes()
    result = run_script(release_layout, "--publish-new-version")
    assert result.returncode == 0, result.stderr
    assert active.resolve() != previous
    assert len(list(releases.iterdir())) == 2
    assert (active / "index.html").read_bytes() == (source / "index.html").read_bytes()
    assert (active / "assets/old-lazy-chunk.js").read_text() == "// old lazy"
    assert (active / "assets/old-font.woff2").read_bytes() == b"old-font"
    assert (previous / "index.html").read_bytes() == previous_index
    assert run_script(release_layout, "--check").returncode == 0


@pytest.mark.parametrize("args", [(), ("--check",), ("--publish-new-version",)])
def test_immutable_asset_name_collision_is_rejected(release_layout, args):
    source, _, _, _ = release_layout
    (source / "assets/index-approved.js").write_text("// incompatible bytes", encoding="utf-8")
    result = run_script(release_layout, *args)
    assert result.returncode != 0
    assert "Immutable asset collision" in result.stderr
    assert_unchanged(release_layout)


def test_initial_publish_and_same_entry_publish(release_layout):
    _, _, previous, active = release_layout
    active.unlink()
    first = run_script(release_layout)
    assert first.returncode == 0, first.stderr
    assert active.resolve() != previous
    assert run_script(release_layout).returncode == 0
    assert run_script(release_layout, "--check").returncode == 0


@pytest.mark.parametrize("running", ["", "false"])
def test_missing_or_stopped_container_is_rejected(release_layout, running):
    result = run_script(release_layout, "--check", running=running)
    assert result.returncode != 0
    assert "missing or not running" in result.stderr
    assert_unchanged(release_layout)
