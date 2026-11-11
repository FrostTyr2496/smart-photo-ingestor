#!/bin/bash
# SessionStart hook: print the handoff pre-flight (task-execution.md §1) so every session,
# including a fresh Haiku one, starts from the same facts. Stdout is added to the session context.
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
echo "## Pre-flight (.claude/hooks/preflight.sh)"
echo "Branch: $(git branch --show-current 2>/dev/null)"
echo "git status --short:"
git status --short 2>/dev/null | head -20
echo "git log -5 --oneline:"
git log -5 --oneline 2>/dev/null
echo "START_HERE.md, In progress:"
awk '/^## In progress/{f=1;next} /^## /{f=0} f' START_HERE.md 2>/dev/null | sed '/^```/,$d' | sed '/^$/d' | head -8
echo "Mounted volumes (read-only for agents): $(ls /Volumes 2>/dev/null | grep -v '^Macintosh HD$' | paste -sd ',' -)"
exit 0
