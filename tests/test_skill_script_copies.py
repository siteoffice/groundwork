"""A skill must stand alone, so two skills keep their own copy of a shared script.

The copies must not drift: siteoffice-cleanup and siteoffice-migrate each carry
Invoke-SiteofficeAudit.ps1, and they are equal byte for byte.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "plugins" / "groundwork" / "skills"
CLEANUP = SKILLS / "siteoffice-cleanup" / "scripts" / "Invoke-SiteofficeAudit.ps1"
MIGRATE = SKILLS / "siteoffice-migrate" / "scripts" / "Invoke-SiteofficeAudit.ps1"


def test_both_audit_script_copies_exist():
    assert CLEANUP.is_file(), CLEANUP
    assert MIGRATE.is_file(), MIGRATE


def test_the_audit_script_copies_are_equal_byte_for_byte():
    a, b = CLEANUP.read_bytes(), MIGRATE.read_bytes()
    assert a == b, (
        "the two Invoke-SiteofficeAudit.ps1 copies differ (%d and %d bytes); "
        "change both, a skill must stand alone so neither can import the other" % (len(a), len(b))
    )
