import re
import subprocess

from compass.eval.gitmeta import git_dirty, git_sha


def test_git_sha_matches_rev_parse() -> None:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    assert git_sha() == head
    assert re.fullmatch(r"[0-9a-f]{40}", git_sha())


def test_git_dirty_returns_bool() -> None:
    assert isinstance(git_dirty(), bool)
