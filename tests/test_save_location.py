"""
Where the plugin saves: a save location on the NAS (\\server\share\folder and its login) that the plugin mounts itself,
with the stream folder and the YouTube clips folder inside it, each changeable; and the container's own folders until
one is set (save_location.py, save_location_routes.py). `mount` is a stand-in: the "share" is a folder of the test's.
"""
from __future__ import annotations

import pytest

from streamer import mounts, save_location


@pytest.fixture()
def linked(tmp_path, monkeypatch):
    """A pretend NAS: mounting the save location makes the share's folder (a folder of the test's) "mounted" at the
    mount point, which is that folder itself. `.mounted`, `.asked` (the specs mounted), `.refuse` (a message to refuse
    with), `.share` (its folder)."""
    class Nas:
        mounted = False
        refuse = None

    n = Nas()
    n.asked = []
    n.share = tmp_path / "share"
    n.share.mkdir()
    monkeypatch.setattr(save_location, "MOUNT_POINT", n.share)
    monkeypatch.setattr(mounts, "is_mounted", lambda p: n.mounted and str(p) == str(n.share))

    def mount(spec, where):
        if n.refuse:
            raise mounts.MountError(n.refuse, "auth")
        n.asked.append(spec)
        n.mounted = True

    def unmount(where, lazy=False):
        n.mounted = False

    monkeypatch.setattr(mounts, "mount", mount)
    monkeypatch.setattr(mounts, "unmount", unmount)
    monkeypatch.setattr(mounts, "mount_capability", lambda: {"ok": True, "message": None, "smb": True, "nfs": True})
    return n


ADDRESS = r"\\192.168.10.240\3d_Printing_Elves\Farmhand"


def test_until_a_save_location_is_set_the_containers_own_folders_are_used(api, plugin, linked):
    page = api.get("/api/save-location").json()
    assert page["address"] is None and page["mounted"] is False
    assert [f["key"] for f in page["folders"]] == ["stream", "clips"]
    assert page["folders"][0]["relative"] is None  # (the container's own: /media)
    assert linked.asked == []


def test_a_save_location_is_mounted_and_its_folders_are_made_inside_it(api, plugin, linked):
    r = api.put("/api/save-location", json={"address": ADDRESS, "username": "3delves", "password": "s3cret"})
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["address"] == ADDRESS and page["mounted"] and page["has_password"] and "password" not in page
    assert linked.asked[0].server == "192.168.10.240" and linked.asked[0].share == "3d_Printing_Elves/Farmhand"
    assert linked.asked[0].username == "3delves" and linked.asked[0].password == "s3cret"
    assert [(f["label"], f["path"]) for f in page["folders"]] == [
        ("Stream folder", ADDRESS + "\\stream"), ("YouTube clips", ADDRESS + "\\YouTube clips")]
    assert (linked.share / "stream").is_dir() and (linked.share / "YouTube clips").is_dir()
    # the stream folder and clips now go there
    assert api.get("/api/stream").json()["media"]["windows_path"] == ADDRESS + "\\stream"
    clips = api.get("/api/clips").json()["folder"]
    assert clips["shown_as"] == ADDRESS + "\\YouTube clips" and clips["problem"] is None
    # the password never comes back, and a new login without one keeps it
    assert "s3cret" not in api.get("/api/save-location").text
    assert api.put("/api/save-location", json={"address": ADDRESS, "username": "3delves"}).status_code == 200
    assert linked.asked[-1].password == "s3cret"


def test_a_folder_is_changed_to_another_inside_the_save_location(api, plugin, linked):
    api.put("/api/save-location", json={"address": ADDRESS, "username": "3delves", "password": "s3cret"})
    (linked.share / "Streams").mkdir()
    listed = api.get("/api/save-location/browse").json()
    assert listed["shown_as"] == ADDRESS and "Streams" in listed["folders"] and "stream" in listed["folders"]
    made = api.post("/api/save-location/browse", json={"path": "Streams", "name": "2026"}).json()
    assert made["path"] == "Streams/2026" and made["shown_as"] == ADDRESS + "\\Streams\\2026"
    page = api.put("/api/save-location/folders/stream", json={"path": "Streams/2026"}).json()
    assert page["folders"][0]["path"] == ADDRESS + "\\Streams\\2026"
    assert api.put("/api/save-location/folders/stream", json={"path": "../etc"}).status_code == 422
    assert api.get("/api/save-location/browse", params={"path": "../.."}).status_code == 422
    assert api.put("/api/save-location/folders/nothing", json={"path": "x"}).status_code == 422


def test_a_save_location_that_wont_mount_says_why_and_keeps_the_old_one(api, plugin, linked):
    api.put("/api/save-location", json={"address": ADDRESS, "username": "3delves", "password": "s3cret"})
    linked.refuse = "The NAS refused the connection: wrong username or password, or that user isn't allowed on this share."
    r = api.put("/api/save-location", json={"address": "\\\\192.168.10.240\\other", "username": "x", "password": "y"})
    assert r.status_code == 422 and "wrong username or password" in r.json()["detail"]
    assert api.get("/api/save-location").json()["address"] == ADDRESS  # (nothing saved)
    assert api.put("/api/save-location", json={"address": "not an address at all//"}).status_code == 422


def test_letting_the_save_location_go_needs_confirming(api, plugin, linked):
    api.put("/api/save-location", json={"address": ADDRESS, "username": "3delves", "password": "s3cret"})
    assert api.delete("/api/save-location").status_code == 409
    page = api.delete("/api/save-location", params={"confirm": "true"}).json()
    assert page["address"] is None and not page["has_password"] and not linked.mounted


def test_the_network_path_is_written_as_windows_writes_it():
    assert save_location.network_path({"address": "//192.168.10.240/3d_Printing_Elves/Farmhand"}, "YouTube clips") == \
        "\\\\192.168.10.240\\3d_Printing_Elves\\Farmhand\\YouTube clips"
    assert save_location.network_path({"address": "smb://nas/farm"}) == "\\\\nas\\farm"
