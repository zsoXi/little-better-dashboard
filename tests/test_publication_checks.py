"""Publication hygiene checks (spec §20 PUB-T01..T07, local verification).

These are real, bounded checks against the working tree: README claims,
launcher bits, secrets/user-data hygiene, screenshot, runtime isolation and
remote-operation hygiene. They never touch the network, never launch a
browser window and never modify tracked files.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _read(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _git(*args, timeout=60):
    return subprocess.run(["git", "-C", str(REPO_ROOT)] + list(args),
                          capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


class TestPublicationChecks(unittest.TestCase):
    def test_pub_t01_readme_matches_tested_behavior(self):
        readme = _read(REPO_ROOT / "README.md").lower()
        for claim in (
                "127.0.0.1",            # local bind
                "token",                # per-instance token / fragment
                "#token=",              # fragment flow
                "unknown",              # unknown outcomes
                "recency",              # recency labels
                "non-additive",         # commit windows warning
                "checkpoint",           # derived cache mention
                ".cache",               # cache location
                "tunnel",               # no tunnel support
                "tail",                 # bounded tail mention
                "safe to delete",       # checkpoint can be deleted
        ):
            self.assertIn(claim, readme, "README must mention %r" % claim)
        self.assertNotIn("guaranteed", readme)
        self.assertNotIn("tested on macos", readme)
        self.assertNotIn("tested on linux", readme)

    def test_pub_t02_shell_launcher_is_executable(self):
        r = _git("ls-files", "-s", "start-dashboard.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.strip().splitlines()[0]
        mode = line.split()[0]
        self.assertEqual(mode, "100755",
                         "start-dashboard.sh must be recorded executable")
        content = _read(REPO_ROOT / "start-dashboard.sh")
        self.assertTrue(content.startswith("#!/bin/sh"))
        self.assertIn('cd "$(dirname "$0")"', content)

    def test_pub_t03_bat_special_paths_and_arg_forwarding(self):
        bat = _read(REPO_ROOT / "start-dashboard.bat")
        self.assertIn('cd /d "%~dp0"', bat)
        self.assertIn("%*", bat)
        sdir = Path(tempfile.mkdtemp(prefix="pub-t03-")) / "dir with space !(x) ünïcode"
        self.addCleanup(shutil.rmtree, str(sdir.parent), True)
        sdir.mkdir(parents=True)
        shutil.copyfile(REPO_ROOT / "start-dashboard.bat", sdir / "start-dashboard.bat")
        shutil.copyfile(REPO_ROOT / "opencode_dashboard.py", sdir / "opencode_dashboard.py")
        env = dict(os.environ)
        env["HOME"] = str(sdir / "home")
        env["USERPROFILE"] = env["HOME"]
        env["LOCALAPPDATA"] = env["HOME"]
        os.makedirs(env["HOME"], exist_ok=True)
        cp = subprocess.run(
            ["cmd", "/c", "start-dashboard.bat", "--help"],
            cwd=str(sdir), capture_output=True, text=True, timeout=60, env=env)
        out = (cp.stdout or "") + (cp.stderr or "")
        self.assertEqual(cp.returncode, 0, out[-800:])
        self.assertIn("usage:", out.lower())

    def test_pub_t04_no_secrets_or_user_data(self):
        r = _git("status", "--porcelain")
        d1 = _git("diff")
        d2 = _git("diff", "--cached")
        blob = r.stdout + d1.stdout + d2.stdout
        patterns = [
            (r"github_pat_[A-Za-z0-9_]{20,}", "github token"),
            (r"\bsk-[A-Za-z0-9]{20,}\b", "openai-style key"),
            (r"\bAKIA[0-9A-Z]{16}\b", "aws key"),
            (r"Bearer\s+[A-Za-z0-9_\-]{32,}", "bearer literal"),
            (r"TYPESAFE_API_KEY\s*=\s*\S+", "typesafe key assignment"),
        ]
        for pat, what in patterns:
            self.assertIsNone(re.search(pat, blob),
                              "possible %s leaked into the diff" % what)
        tracked = _git("ls-files").stdout.lower().splitlines()
        for name in tracked:
            self.assertFalse(name.endswith(".sqlite3") or name.endswith(".db"),
                             "database file tracked: %s" % name)
        art = REPO_ROOT / "artifacts"
        if art.is_dir():
            for log in art.glob("*.log"):
                text = _read(log)
                self.assertIsNone(
                    re.search(r"Bearer\s+[A-Za-z0-9_\-]{32,}(?!\s*['\"]?\s*\+)", text),
                    "literal bearer token found in %s" % log.name)

    def test_pub_t05_screenshot_current(self):
        shot = REPO_ROOT / "screenshot.png"
        self.assertTrue(shot.is_file(), "screenshot.png must exist")
        self.assertGreater(shot.stat().st_size, 10_000,
                           "screenshot.png looks empty/stale")
        readme = _read(REPO_ROOT / "README.md")
        self.assertIn("screenshot.png", readme)

    def test_pub_t06_runtime_stays_dependency_free(self):
        src = _read(REPO_ROOT / "opencode_dashboard.py")
        self.assertNotIn("import playwright", src)
        self.assertNotIn("from playwright", src)
        req = _read(REPO_ROOT / "requirements-dev.txt")
        self.assertIn("playwright==", req)
        self.assertIn("# dev-only", req)

    def test_pub_t07_no_unauthorized_remote_operations(self):
        r = _git("reflog", "--date=iso")
        text = (r.stdout or "").lower()
        self.assertNotIn("push", text, "no push may appear in the reflog")
        self.assertNotIn("force", text)
        st = _git("status", "--porcelain")
        self.assertNotIn("?? ..", st.stdout)


if __name__ == "__main__":
    unittest.main()
