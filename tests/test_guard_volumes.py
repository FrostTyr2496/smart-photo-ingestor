"""The /Volumes guard hook must allow plain reads and block anything that could change a card or drive."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "guard_volumes.py"


def run(payload: dict) -> int:
    proc = subprocess.run(
        [sys.executable, "-I", str(HOOK)], input=json.dumps(payload), capture_output=True, text=True
    )
    return proc.returncode


def bash(cmd: str, cwd: str = "/Users/someone/repo") -> int:
    return run({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd})


@pytest.mark.parametrize("cmd", [
    "ls -la /Volumes/",
    "ls '/Volumes/Card A/DCIM'",
    "find '/Volumes/X9 Pro' -name '*.NEF' -type f | wc -l",
    "stat -f %z /Volumes/Card/DCIM/100NZ6_2/DSC_0001.NEF",
    "exiftool -s -Model -SerialNumber -ShutterCount /Volumes/Card/DCIM/100NZ6_2/DSC_0001.NEF",
    "exiftool -q -T -FileName -r /Volumes/Card/DCIM > /tmp/out.tsv",
    "diskutil info '/Volumes/X9 Pro'",
    "diskutil list external",
    "cp /Volumes/Card/DCIM/100NZ6_2/DSC_0001.NEF /tmp/sample/",
    "rsync -a /Volumes/Card/DCIM/ /tmp/sample/",
    "sqlite3 -readonly '/Volumes/Flats 1/lrcat/Lightroom Catalog.lrcat' 'select 1'",
    "shasum -a 256 /Volumes/Card/x.NEF 2>/dev/null",
    "uv run pytest tests -q",
    "git status --short",
    "rm -rf /tmp/scratch",
])
def test_reads_are_allowed(cmd):
    assert bash(cmd) == 0, cmd


@pytest.mark.parametrize("cmd", [
    "rm /Volumes/Card/DCIM/100NZ6_2/DSC_0001.NEF",
    "rm -rf '/Volumes/X9 Pro/Photography'",
    "mv /Volumes/Card/a.NEF /tmp/",
    "touch /Volumes/Card/x",
    "mkdir -p '/Volumes/X9 Pro/Imports'",
    "cp /tmp/a.NEF '/Volumes/X9 Pro/a.NEF'",
    "rsync -a --remove-source-files /Volumes/Card/DCIM/ /tmp/x/",
    "find /Volumes/Card -name '._*' -delete",
    "find /Volumes/Card -name '*.tmp' -exec rm {} +",
    "exiftool -Artist=me /Volumes/Card/a.NEF",
    "exiftool -overwrite_original -Copyright=x /Volumes/Card/a.JPG",
    "exiftool -o /tmp/x.jpg /Volumes/Card/a.JPG",
    "echo hi > /Volumes/Card/note.txt",
    "ls /Volumes > '/Volumes/X9 Pro/list.txt'",
    "diskutil eject /Volumes/Card",
    "diskutil eraseVolume ExFAT CARD /Volumes/Card",
    "xattr -w com.x y /Volumes/Card/a.NEF",
    "xattr -c /Volumes/Card/a.NEF",
    "sqlite3 '/Volumes/Flats 1/lrcat/Lightroom Catalog.lrcat' 'delete from AgLibraryFile'",
    "cd /Volumes/Card && rm DSC_0001.NEF",
    "cd '/Volumes/X9 Pro'; touch x",
    "ls /tmp\nrm /Volumes/Card/a.NEF",
    "uv run photovault ingest --source /Volumes/Card --target /tmp/out",
    "python3 script.py /Volumes/Card",
    "ls $(echo /Volumes/Card)",
    "sudo ls /Volumes/Card",
    "tee /Volumes/Card/x < /tmp/y",
    "find /Volumes/Card -name '*.NEF' | xargs rm",
])
def test_writes_are_blocked(cmd):
    assert bash(cmd) == 2, cmd


def test_git_and_heredoc_text_mentioning_volumes():
    msg = "git commit -q -F - <<'EOF'\nfix: guard writes on /Volumes\n\nmore about /Volumes/Card\nEOF\ngit log -1"
    assert bash(msg) == 0
    assert bash('git commit -m "guard /Volumes writes"') == 0
    assert bash("cat > /tmp/notes.md <<'EOF'\nthe card is at /Volumes/Card\nEOF") == 0
    assert bash("git -C /Volumes/X9/repo status") == 2
    assert bash("python3 - <<'EOF'\nimport os; os.remove('/Volumes/Card/a.NEF')\nEOF") == 2
    assert bash("cat <<EOF > /Volumes/Card/x\nhello\nEOF") == 2
    assert bash("git status", cwd="/Volumes/Card") == 2


def test_relative_write_when_cwd_is_on_a_volume():
    assert bash("rm DSC_0001.NEF", cwd="/Volumes/Card/DCIM/100NZ6_2") == 2
    assert bash("ls -la", cwd="/Volumes/Card/DCIM/100NZ6_2") == 0
    assert bash("echo x > note.txt", cwd="/Volumes/Card") == 2


@pytest.mark.parametrize("tool,key", [("Write", "file_path"), ("Edit", "file_path"),
                                      ("NotebookEdit", "notebook_path")])
def test_file_tools_blocked_on_volumes(tool, key):
    assert run({"tool_name": tool, "tool_input": {key: "/Volumes/Card/notes.md"}}) == 2
    assert run({"tool_name": tool, "tool_input": {key: "/tmp/notes.md"}}) == 0


def test_other_tools_pass():
    assert run({"tool_name": "Read", "tool_input": {"file_path": "/Volumes/Card/a.JPG"}}) == 0
