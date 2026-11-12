"""Offline guards for the dependency policy; no YAML or pipeline imports needed."""

from pathlib import Path
import re
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LOCKS = ("requirements.txt", "requirements-search.txt", "scripts/lock-tools.txt")
# pip-compile writes the wheels-only resolver option into each lock file, so any install from it
# refuses source builds even without the flag on the command line.
LOCK_OPTIONS = ["--only-binary :all:"]
WORKFLOWS = {
    "update.yml": "requirements.txt",
    "search-data.yml": "requirements-search.txt",
}


def test_dependency_scripts_have_valid_shell_syntax():
    # Parse only: never execute Docker, installers, or the embedded test commands. The script the
    # container runs is a quoted heredoc, which the outer parse treats as data, so it is checked on
    # its own as well.
    for name in ("lock-requirements.sh", "check-locked-install.sh"):
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        body = re.search(r"<<'SCRIPT'\n(.*?)\nSCRIPT\n", text, re.S)
        assert body, f"{name}: container script heredoc not found"
        for label, kwargs in (
            ("outer script", {"args": ["sh", "-n", str(ROOT / "scripts" / name)]}),
            ("container script", {"args": ["sh", "-n"], "input": body.group(1)}),
        ):
            result = subprocess.run(capture_output=True, text=True, check=False, **kwargs)
            assert result.returncode == 0, f"{name} {label}: {result.stderr}"


def test_lock_entries_are_exact_pins_with_sha256_hashes():
    for name in LOCKS:
        text = (ROOT / name).read_text(encoding="utf-8")
        entries = []
        pending = ""
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            continued = line.endswith("\\")
            pending += " " + (line[:-1] if continued else line)
            if continued:
                continue
            entries.append(pending.strip())
            pending = ""
        assert not pending, f"{name}: unterminated continuation"
        options = [entry for entry in entries if entry.startswith("--")]
        packages = [entry for entry in entries if not entry.startswith("--")]
        assert options == LOCK_OPTIONS, f"{name}: unexpected lock options: {options}"
        assert packages, f"{name}: empty lock file"
        for entry in packages:
            tokens = shlex.split(entry)
            assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9.+!-]+", tokens[0]), (
                f"{name}: expected an exact package pin: {entry}"
            )
            assert len(tokens) > 1, f"{name}: missing hash for {tokens[0]}"
            assert all(re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", token) for token in tokens[1:]), (
                f"{name}: invalid hash or unsupported lock syntax: {entry}"
            )


def test_workflows_keep_pinned_actions_and_locked_installs():
    # Deliberately guard the current one-line install syntax, not arbitrary YAML.
    # A switch to multiline commands or another installer needs an explicit review.
    for name, lock in WORKFLOWS.items():
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines() if not line.lstrip().startswith("#")]
        actions = [line for line in lines if re.match(r"(?:-\s+)?uses:", line)]
        assert actions, f"{name}: no action references found"
        for line in actions:
            ref = line.split("uses:", 1)[1].split("#", 1)[0].strip().strip("\"'")
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), (
                f"{name}: action must use a full commit SHA: {ref}"
            )
        installs = [line for line in lines if re.search(r"\bpip(?:3)?\s+install\b", line)]
        expected = f"pip install --require-hashes --only-binary=:all: -r {lock}"
        assert len(installs) == 1, f"{name}: review changed install steps: {installs}"
        assert installs[0] in (f"- run: '{expected}'", f'- run: "{expected}"'), (
            f"{name}: install must be quoted and enforce hashes and wheels: {installs[0]}"
        )
