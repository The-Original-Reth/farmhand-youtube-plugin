"""
The plugin's settings copied to Farmhand (stream:backup), and given back by Farmhand's Plugins page's Restore: pushed
once after each start and then only when they change, without the keys that mustn't travel or be restored (backup.NOT_KEPT).
"""
from __future__ import annotations

import json

from streamer import backup, save_location, youtube_live
from streamer.live_stream.constants import SETTINGS_KEY, STREAM_KEY_KEY
from streamer.live_stream.runner import LIVE_KEY
from streamer.live_stream.sources import CAMERAS_KEY


def set_up(s):
    """A plugin an operator has set up: stream settings, key, YouTube sign-in, a save location with its password, and
    what changes by itself (live, the cameras last heard of)."""
    s.put(SETTINGS_KEY, json.dumps({"cols": 2, "rows": 1, "title": "The farm"}))
    s.put(STREAM_KEY_KEY, "abcd-efgh-ijkl")
    s.put(youtube_live.CLIENT_KEY, json.dumps({"client_id": "123.apps.googleusercontent.com", "client_secret": "GOCSPX-x"}))
    s.put(youtube_live.TOKEN_KEY, "1//refresh")
    s.put(youtube_live.CHANNEL_KEY, "Print Farm")
    s.put_json(save_location.LOCATION_KEY, {"address": "//nas/farm/stream", "username": "farm", "domain": None, "version": "",
                                            "folders": {"stream": "stream", "clips": "YouTube clips"}})
    s.put(save_location.PASSWORD_KEY, "nas-password")
    s.put(LIVE_KEY, "1")
    s.put_json(CAMERAS_KEY, {"1": {"name": "VT 1190", "cameras": []}})
    s.commit()


# ------------------------------------------------------------------ pushing


def test_the_settings_are_pushed_without_the_keys_that_stay_here(plugin, farmhand):
    set_up(plugin)
    assert backup.push() == "pushed"
    [sent] = farmhand.backups_put
    assert sent["format"] == 1 and sent["saved_at"]
    kept = sent["settings"]
    assert kept[STREAM_KEY_KEY] == "abcd-efgh-ijkl" and kept[youtube_live.TOKEN_KEY] == "1//refresh"
    assert kept[youtube_live.CLIENT_KEY] and kept[save_location.LOCATION_KEY] and kept[SETTINGS_KEY]
    for key in (LIVE_KEY, save_location.PASSWORD_KEY, CAMERAS_KEY):
        assert key not in kept
    assert backup.status() == {"saved_at": sent["saved_at"], "error": None}


def test_pushed_only_when_something_changed(plugin, farmhand):
    set_up(plugin)
    assert backup.push() == "pushed"
    assert backup.push() == "same"
    plugin.put(LIVE_KEY, None)  # (changes to what isn't kept don't count)
    plugin.put_json(CAMERAS_KEY, {})
    assert backup.push() == "same"
    plugin.put(STREAM_KEY_KEY, "new-key")
    assert backup.push() == "pushed"
    assert len(farmhand.backups_put) == 2 and farmhand.backup["settings"][STREAM_KEY_KEY] == "new-key"


def test_once_after_each_start(plugin, farmhand):
    set_up(plugin)
    backup.push()
    backup.reset()  # (the plugin started again: what it pushed is remembered only in memory)
    assert backup.push() == "pushed"
    assert len(farmhand.backups_put) == 2


def test_without_the_permission_it_says_so_and_waits(plugin, farmhand):
    set_up(plugin)
    farmhand.permissions.discard("stream:backup")
    assert backup.push() == "failed"
    assert "stream:backup" in backup.status()["error"] and "Plugins page" in backup.status()["error"]
    assert backup.push() == "waiting"  # (not asked again every 30 s: every few minutes)
    farmhand.permissions.add("stream:backup")
    assert backup.push(force=True) == "pushed"
    assert backup.status()["error"] is None


def test_farmhand_down_is_tried_again_at_the_next_look(plugin, farmhand):
    set_up(plugin)
    farmhand.down = True
    assert backup.push() == "failed"
    assert "Farmhand" in backup.status()["error"]
    farmhand.down = False
    assert backup.push() == "pushed"  # (no waiting: Farmhand was only away)


def test_the_page_says_how_the_backup_is(api, plugin, farmhand):
    backup.push()
    assert api.get("/api/stream").json()["backup"]["saved_at"] == farmhand.backup["saved_at"]


def test_no_secret_in_what_the_plugin_prints(plugin, farmhand, capsys):
    set_up(plugin)
    farmhand.permissions.discard("stream:backup")
    backup.push()
    farmhand.permissions.add("stream:backup")
    backup.push(force=True)
    out = capsys.readouterr()
    assert "abcd-efgh-ijkl" not in out.out + out.err and "1//refresh" not in out.out + out.err


# ------------------------------------------------------------------ restoring


def test_restore_puts_farmhands_copy_in_place_keeping_what_stays_here(api, plugin, farmhand):
    set_up(plugin)
    farmhand.backup = {"format": 1, "saved_at": "2026-11-19T09:00:00+00:00", "settings": {
        SETTINGS_KEY: json.dumps({"cols": 1, "rows": 1, "title": "Restored"}),
        STREAM_KEY_KEY: "restored-key",
        youtube_live.TOKEN_KEY: "1//restored",
        LIVE_KEY: "", save_location.PASSWORD_KEY: "from-the-copy", CAMERAS_KEY: "{}",  # (never taken from a copy)
    }}
    r = api.post("/api/farmhand-backup/restore", json={"saved_at": "2026-11-19T09:00:00+00:00"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["restored"] is True and got["saved_at"] == "2026-11-19T09:00:00+00:00" and "next Go live" in got["note"]
    assert plugin.get(STREAM_KEY_KEY) == "restored-key" and plugin.get(youtube_live.TOKEN_KEY) == "1//restored"
    assert json.loads(plugin.get(SETTINGS_KEY))["title"] == "Restored"
    assert plugin.get(youtube_live.CHANNEL_KEY) is None  # (not in the copy: gone, as it was when the copy was made)
    assert plugin.get(save_location.LOCATION_KEY) is None
    assert plugin.get(LIVE_KEY) == "1" and plugin.get(save_location.PASSWORD_KEY) == "nas-password"
    assert plugin.get(CAMERAS_KEY) == json.dumps({"1": {"name": "VT 1190", "cameras": []}})
    on_disk = json.loads(plugin.path.read_text(encoding="utf-8"))  # (written to the file, too)
    assert on_disk[STREAM_KEY_KEY] == "restored-key"
    assert backup.push() == "same"  # (Farmhand has it already: not pushed straight back)
    assert backup.status()["saved_at"] == "2026-11-19T09:00:00+00:00"


def test_a_restored_save_location_asks_for_its_password_again(api, plugin, farmhand):
    location = {"address": "//nas/farm/stream", "username": "farm", "folders": {}}
    farmhand.backup = {"format": 1, "saved_at": "2026-11-19T09:00:00+00:00",
                       "settings": {save_location.LOCATION_KEY: json.dumps(location)}}
    r = api.post("/api/farmhand-backup/restore", json={})
    assert r.status_code == 200 and "password again" in r.json()["note"]


def test_restore_with_no_copy_in_farmhand(api, plugin, farmhand):
    set_up(plugin)
    r = api.post("/api/farmhand-backup/restore", json={"saved_at": None})
    assert r.status_code == 404 and "no copy" in r.json()["detail"]
    assert plugin.get(STREAM_KEY_KEY) == "abcd-efgh-ijkl"  # (nothing changed)


def test_restore_without_the_permission(api, plugin, farmhand):
    farmhand.backup = {"format": 1, "saved_at": "2026-11-19T09:00:00+00:00", "settings": {}}
    farmhand.permissions.discard("stream:backup")
    r = api.post("/api/farmhand-backup/restore", json={})
    assert r.status_code == 403 and "stream:backup" in r.json()["detail"] and "Allow" in r.json()["detail"]


def test_restore_with_farmhand_down(api, plugin, farmhand):
    farmhand.down = True
    r = api.post("/api/farmhand-backup/restore", json={})
    assert r.status_code == 502 and "Farmhand" in r.json()["detail"]


def test_restore_refuses_a_copy_it_cant_read(api, plugin, farmhand):
    set_up(plugin)
    farmhand.backup = {"format": 2, "saved_at": "2026-11-19T09:00:00+00:00", "settings": {}}
    r = api.post("/api/farmhand-backup/restore", json={})
    assert r.status_code == 422 and "nothing was changed" in r.json()["detail"]
    farmhand.backup = {"format": 1, "saved_at": "2026-11-19T09:00:00+00:00", "settings": {STREAM_KEY_KEY: 5}}
    r = api.post("/api/farmhand-backup/restore", json={})
    assert r.status_code == 422
    assert plugin.get(STREAM_KEY_KEY) == "abcd-efgh-ijkl"
