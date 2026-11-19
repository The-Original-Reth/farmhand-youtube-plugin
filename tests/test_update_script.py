"""
update.sh, run against a stand-in for docker (and sleep): the new image built first; a swap Docker couldn't finish
(the old container not stopping at once, the new one left made but not started) tried again, the half-made one cleared
away, until the new container is up and well; a failed build changing nothing.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("sh"), reason="needs a POSIX shell")

# The stand-in: every call logged; `docker compose up` fails the first FAIL_UPS times, leaving a half-made container
# behind, as Docker does when it couldn't stop the old one
FAKE_DOCKER = r"""#!/bin/sh
echo "$*" >> "$LOG"
case "$*" in
  "compose build --pull") [ -n "${BUILD_FAILS:-}" ] && exit 1; exit 0 ;;
  "compose up -d --build --remove-orphans")
    n=$(cat "$STATE/ups" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$STATE/ups"
    if [ "$n" -le "${FAIL_UPS:-0}" ]; then echo "1a2b3c4d5e6f_$NAME" > "$STATE/leftover"; exit 1; fi
    touch "$STATE/running"; exit 0 ;;
  "inspect -f {{.State.Running}} $NAME") [ -f "$STATE/running" ] && echo true || echo false; exit 0 ;;
  ps\ -a*) cat "$STATE/leftover" 2>/dev/null; exit 0 ;;
  rm\ -f*) rm -f "$STATE/leftover"; exit 0 ;;
  inspect\ -f*) echo healthy; exit 0 ;;
  "compose ps") exit 0 ;;
esac
echo "unexpected: $*" >&2
exit 3
"""


@pytest.fixture()
def folder(tmp_path):
    shutil.copy(ROOT / "update.sh", tmp_path / "update.sh")
    shutil.copy(ROOT / "docker-compose.yml", tmp_path / "docker-compose.yml")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n")  # (no waiting in tests)
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    (tmp_path / "state").mkdir()
    return tmp_path


def run(folder: Path, **env) -> tuple[subprocess.CompletedProcess, list[str]]:
    name = next(line.split(":", 1)[1].split("#")[0].strip() for line in (folder / "docker-compose.yml").read_text().splitlines()
                if line.strip().startswith("container_name:"))
    environ = {**os.environ, "PATH": f"{folder / 'bin'}:{os.environ['PATH']}", "LOG": str(folder / "log"),
               "STATE": str(folder / "state"), "NAME": name, **env}
    done = subprocess.run(["sh", str(folder / "update.sh")], capture_output=True, text=True, env=environ, timeout=60)
    log = (folder / "log").read_text().splitlines() if (folder / "log").exists() else []
    return done, log


def test_an_update_builds_first_then_swaps_the_container(folder):
    done, log = run(folder)
    assert done.returncode == 0, done.stderr
    assert log[0] == "compose build --pull" and log.count("compose up -d --build --remove-orphans") == 1


def test_a_swap_docker_couldnt_finish_is_tried_again_and_the_half_made_container_cleared(folder):
    done, log = run(folder, FAIL_UPS="2")
    assert done.returncode == 0, done.stderr
    assert log.count("compose up -d --build --remove-orphans") == 3  # (failed twice, then up)
    assert sum(1 for line in log if line.startswith("rm -f 1a2b3c4d5e6f_")) == 2
    assert "trying again" in done.stdout and not (folder / "state" / "leftover").exists()


def test_a_failed_build_changes_nothing(folder):
    done, log = run(folder, BUILD_FAILS="1")
    assert done.returncode == 1 and "nothing was changed" in done.stderr
    assert "compose up -d --build --remove-orphans" not in log


def test_it_gives_up_after_a_while_saying_where_to_look(folder):
    done, log = run(folder, FAIL_UPS="99")
    assert done.returncode == 1 and "didn't start after 8 tries" in done.stderr
    assert log.count("compose up -d --build --remove-orphans") == 8
