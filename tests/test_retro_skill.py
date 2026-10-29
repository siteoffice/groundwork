import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"
sys.path.insert(0, str(SCRIPTS))

SKILL = SCRIPTS.parent / "SKILL.md"


def _desc():
    m = re.search(r"^description: (.*)$", SKILL.read_text(encoding="utf-8"), re.M)
    return m.group(1)


def test_description_is_short_and_routes():
    d = _desc()
    assert len(d) < 1024
    for trigger in ("retro", "monthly checkup of my sessions", "what went wrong in my sessions", "improve my setup"):
        assert '"%s"' % trigger in d
    for other in ("siteoffice-checkup", "audit-my-context", "diagnosing-superpowers"):
        assert other in d.split("Do NOT use")[1]
    assert "apply" in d.split("Do NOT use")[1]


def test_body_states_scope_and_commands():
    text = SKILL.read_text(encoding="utf-8")
    assert all(ord(c) < 128 for c in text)
    assert "\r" not in text
    assert len(text.splitlines()) <= 120
    for needle in ("R1 measures only", "R2", "retro_config.py", "retro_report.py", "register_retro_task.ps1",
                   ".env", "GROUNDWORK_RETRO_LIVE=1", "SKIPPED", "-Register", "-Verify",
                   "retro.launchd.plist.example", "retro.cron.example", "separate worlds analysed"):
        assert needle in text, needle


def test_cited_flags_exist_in_the_scripts():
    text = SKILL.read_text(encoding="utf-8")
    sources = {p.name: p.read_text(encoding="utf-8") for p in SCRIPTS.glob("*")
               if p.suffix in (".py", ".ps1")}
    py = sources["retro_config.py"] + sources["retro_report.py"]
    for flag in set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", text)):
        assert flag in py, flag
    for flag in set(re.findall(r"(?<![\w-])-(?:Root|Register|Verify)\b", text)):
        assert flag[1:] in sources["register_retro_task.ps1"], flag
