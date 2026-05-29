"""HEAD sha + dirty flag of the *invoking* repo (cwd). Shared by the
Stage-7 eval CLI and the Stage-8 adversarial CLI so run accounting records
identical provenance."""

import subprocess


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    except subprocess.CalledProcessError:
        return "unknown"


def git_dirty() -> bool:
    try:
        out = subprocess.check_output(["git", "status", "--porcelain"]).decode().strip()
        return bool(out)
    except subprocess.CalledProcessError:
        return False
