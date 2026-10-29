"""Materialize the retro fixtures with real-format secrets built at runtime.

The committed fixtures hold only @@SECRET:<kind>@@ placeholders, so the secrets
scan of the git lane never sees a secret-shaped line. Every value below is built
from fragments for the same reason. Kinds match the Task 3 scrubber patterns.
"""
import re
import shutil
from pathlib import Path
from typing import Dict

FIX = Path(__file__).resolve().parent / "fixtures" / "retro"
PLACEHOLDER = re.compile(r"@@SECRET:([a-z_]+)@@")


def secret_values() -> Dict[str, str]:
    return {
        "private_key": "-----BEGIN" + " RSA " + "PRIVATE KEY-----",
        "aws_access_key": "AK" + "IA" + "IOSFODNN7EXAMPLE",
        "github_pat": "github" + "_pat_" + "11ABCDEFG0123456789_abcdefghij",
        "github_token": "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
        "anthropic_key": "sk-" + "ant-" + "api03-abcdefghijklmnopqrstuv",
        "stripe_key": "sk" + "_live_" + "abcdefghijklmnopqrstuvwx",
        "openai_key": "sk-" + "proj-" + "abcdefghijklmnopqrstuvwx",
        "slack_token": "xo" + "xb-" + "1234567890-abcdefghij",
        "google_api_key": "AI" + "za" + "SyA1234567890abcdefghijklmnopqrstuv",
        "jwt": "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0In0" + "." + "abcdefghijklmnop",
        "connection_string": "postgres://demo:" + "s3cretpass@localhost/db",
        "password_assignment": "pass" + "word=" + "correcthorsebattery9",
        "high_entropy": "Zq8Lm2Xv9Rt4Wp7Ys3Kd6Nb1" + "Hf5Jc0GxTv4Qe",
    }


def materialize_fixtures(dest: Path) -> Path:
    """Copy tests/fixtures/retro to dest and swap each placeholder for its value."""
    dest = Path(dest)
    shutil.copytree(FIX, dest, dirs_exist_ok=True)
    values = secret_values()
    for p in dest.rglob("*"):
        if p.is_file():
            text = p.read_bytes().decode("ascii")
            new = PLACEHOLDER.sub(lambda m: values[m.group(1)], text)
            if new != text:
                p.write_bytes(new.encode("ascii"))
    return dest
