"""The marketplace text of groundwork-mods names what the plugin holds, as the README says.

The plugin holds the Setup Doctor and the agent policy, plus the Fleet and the Git lane behind
two switches that are off by default (AGENTS.md, plugins/groundwork-mods/README.md). The
marketplace listing is what a person reads before installing, so it must say the same.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MARKET = ROOT / ".claude-plugin" / "marketplace.json"
MODS_PLUGIN = ROOT / "plugins" / "groundwork-mods" / ".claude-plugin" / "plugin.json"


def mods_entry():
    market = json.loads(MARKET.read_text(encoding="utf-8"))
    return next(p for p in market["plugins"] if p["name"] == "groundwork-mods")


def test_marketplace_text_names_the_fleet_and_the_git_lane_as_switches_off_by_default():
    text = mods_entry()["description"]
    for want in ("Setup Doctor", "agent policy", "Fleet", "Git lane", "switch", "off by default"):
        assert want in text, "the groundwork-mods marketplace text lacks %r: %s" % (want, text)


def test_marketplace_text_starts_like_the_plugin_description():
    plugin = json.loads(MODS_PLUGIN.read_text(encoding="utf-8"))["description"]
    first = plugin.split(". ")[0]
    assert mods_entry()["description"].startswith(first), first


def test_marketplace_text_is_plain_ascii_without_em_dash():
    text = mods_entry()["description"]
    assert all(ord(c) < 128 for c in text), text
