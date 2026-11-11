#!/usr/bin/env python3
"""PreToolUse guard: agents may READ /Volumes (camera cards, photo drives) but never change them.

Wired in .claude/settings.json for Bash, Edit, Write, NotebookEdit and MultiEdit.
Exit 0 allows the call; exit 2 blocks it and stderr is shown to the agent.

Real-drive runs (ingest, volume assignment, indexing) are [user] steps: the agent prints the exact
command for the user instead. Tests live in tests/test_guard_volumes.py — keep them green when editing.
The rule of thumb: if a command touches /Volumes and this script can't prove it is read-only, block.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

VOL = "/Volumes/"

# Verbs that only read. cp/ditto/rsync are allowed only when the destination is NOT under /Volumes.
READ_ONLY = {
    "ls", "stat", "du", "df", "file", "head", "tail", "cat", "less", "wc", "sort", "uniq", "cut",
    "tr", "awk", "grep", "egrep", "rg", "shasum", "sha256sum", "md5", "cmp", "diff", "exiftool",
    "diskutil", "mdls", "xattr", "sqlite3", "find", "echo", "printf", "test", "[", "realpath",
    "readlink", "basename", "dirname", "tree", "jq", "mount", "true", "cp", "ditto", "rsync",
}
WRAPPERS = {"command", "time", "nohup", "nice", "env"}
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<>"}
FIND_WRITES = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
EXIFTOOL_WRITE = re.compile(
    r"^-(o|out|w|W|overwrite_original\w*|tagsfromfile|delete_original\w*|restore_original|geotag|srcfile)$"
    r"|^-[^\s]*[<=]",  # -TAG=value, -TAG+=value, -TAG<SRC
    re.IGNORECASE,
)
DISKUTIL_READ = {"info", "list", "apfs"}  # `diskutil apfs list` only; other apfs verbs blocked below
# Heredoc bodies are text, not paths, when fed to these verbs (commit messages, notes written with cat).
# Any other verb fed a heredoc that mentions /Volumes (e.g. `python3 - <<EOF`) is blocked.
HEREDOC_TEXT_OK = {"git", "cat"}
HEREDOC_MARK = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
HEREDOC_VOL, HEREDOC_PLAIN = "__PV_HEREDOC_VOL__", "__PV_HEREDOC__"
MESSAGE = (
    "Agents only READ /Volumes (camera cards and photo drives). Real-drive runs are [user] steps: "
    "give the user the exact command instead. If this is a false positive, split the command so the "
    "/Volumes part is a plain read (ls/stat/find/exiftool without write flags)."
)


def block(reason: str) -> None:
    print(f"Blocked by .claude/hooks/guard_volumes.py: {reason}. {MESSAGE}", file=sys.stderr)
    sys.exit(2)


def under_volumes(path: str, cwd_in_volumes: bool) -> bool:
    if path.startswith(VOL) or path == "/Volumes":
        return True
    return cwd_in_volumes and not path.startswith(("/", "&", "-")) and path not in ("",)


def check_path_tool(tool_input: dict) -> None:
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    real = os.path.realpath(path) if path else ""
    if real.startswith(VOL) or path.startswith(VOL):
        block(f"file edit under /Volumes ({path})")


def strip_heredocs(command: str) -> str:
    """Drop heredoc bodies, leaving a placeholder token that says whether the body mentioned /Volumes."""
    lines, out, pending = command.split("\n"), [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        marks = HEREDOC_MARK.findall(line)
        if not marks:
            out.append(line)
            i += 1
            continue
        bodies_vol = []
        i += 1
        for dash, _q, term in marks:
            body = []
            while i < len(lines) and (lines[i].lstrip("\t") if dash else lines[i]) != term:
                body.append(lines[i])
                i += 1
            i += 1  # skip the terminator line
            bodies_vol.append(any("/Volumes" in b for b in body))
        it = iter(bodies_vol)
        out.append(HEREDOC_MARK.sub(lambda _m: " " + (HEREDOC_VOL if next(it) else HEREDOC_PLAIN) + " ", line))
    return "\n".join(out + pending)


def segments(tokens: list[str]) -> list[list[str]]:
    out, cur = [], []
    for tok in tokens:
        if tok in SEPARATORS:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def positional(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("-")]


def check_segment(seg: list[str], ctx: bool, cmd_touches: bool) -> bool:
    """Validate one simple command. Returns the updated 'cwd is under /Volumes' context.

    cmd_touches: the whole command line mentions /Volumes, so EVERY segment must be read-only
    (this catches `find /Volumes/... | xargs rm`, where the rm segment never names the path).
    """
    # Redirections first: a write redirect into /Volumes is never OK.
    for i, tok in enumerate(seg):
        if tok in REDIRECTS and i + 1 < len(seg):
            target = seg[i + 1]
            if target.startswith("&") or target == "/dev/null":
                continue
            if under_volumes(target, ctx):
                block(f"output redirect into /Volumes ({target})")
    words = [t for t in seg if t not in REDIRECTS and t != "<"]
    while words and ("=" in words[0] and not words[0].startswith("-") or words[0] in WRAPPERS):
        words = words[1:]
    if not words:
        return ctx
    verb, args = os.path.basename(words[0]), words[1:]
    if HEREDOC_VOL in args and verb not in HEREDOC_TEXT_OK:
        block(f"a heredoc that mentions /Volumes is fed to '{verb}'")
    args = [a for a in args if a not in (HEREDOC_VOL, HEREDOC_PLAIN)]
    if verb == "git":
        # Commit messages often mention /Volumes; only real path arguments (or cwd) count.
        if ctx or any(a.startswith("/Volumes") for a in args):
            block("git with a /Volumes path or working directory")
        return ctx
    touches = ctx or cmd_touches

    if verb in ("cd", "pushd"):
        target = args[0] if args else os.path.expanduser("~")
        if target.startswith(VOL) or target == "/Volumes":
            return True
        return ctx and not target.startswith("/")
    if verb == "popd":
        return ctx
    if not touches:
        return ctx
    if verb == "sudo":
        block("sudo with /Volumes")
    if verb not in READ_ONLY:
        block(f"'{verb}' is not a read-only command and the call involves /Volumes")
    if verb == "find" and FIND_WRITES.intersection(args):
        block("find with a write/exec action")
    if verb == "exiftool" and any(EXIFTOOL_WRITE.search(a) for a in args):
        block("exiftool with a write option")
    if verb == "diskutil":
        sub = positional(args)[:2]
        if not sub or sub[0] not in DISKUTIL_READ or (sub[0] == "apfs" and sub[1:] != ["list"]):
            block(f"diskutil {' '.join(sub) or '(no verb)'} is not read-only")
    if verb == "xattr" and any(a.startswith("-") and set(a[1:]) & set("wdc") for a in args):
        block("xattr write/delete/clear")
    if verb == "sqlite3" and not any(
        a == "-readonly" or "mode=ro" in a or "immutable=1" in a for a in args
    ):
        block("sqlite3 on /Volumes without -readonly")
    if verb in ("cp", "ditto", "rsync"):
        if any(a.startswith(("--remove-source-files", "--delete")) for a in args):
            block(f"{verb} with a source-removing/deleting option")
        pos = positional(args)
        if not pos or under_volumes(pos[-1], ctx):
            block(f"{verb} with a destination under /Volumes")
    return ctx


def check_bash(command: str, cwd: str) -> None:
    ctx = cwd.startswith(VOL)
    if VOL not in command and "/Volumes" not in command and not ctx and "cd" not in command:
        return
    if "$(" in command or "`" in command or "<(" in command or ">(" in command:
        if "/Volumes" in command or ctx:
            block("command substitution or process substitution together with /Volumes")
    command = strip_heredocs(command)
    try:
        lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError as exc:
        if "/Volumes" in command or ctx:
            block(f"could not parse the command ({exc})")
        return
    cmd_touches = "/Volumes" in command.replace(HEREDOC_VOL, "")
    for seg in segments(tokens):
        ctx = check_segment(seg, ctx, cmd_touches)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        block("hook input was not JSON")
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        check_path_tool(tool_input)
    elif tool == "Bash":
        check_bash(tool_input.get("command", ""), data.get("cwd", "") or "")
    sys.exit(0)


if __name__ == "__main__":
    main()
