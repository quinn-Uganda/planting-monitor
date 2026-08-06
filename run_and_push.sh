#!/bin/bash
# Daily: sync with the cloud, regenerate the planting status, then publish to GitHub.
# Invoked by launchd. Conflict-safe so it can coexist with the GitHub Actions job
# (whichever fires first refreshes the data; the other just finds nothing to do).
export HOME=/Users/quinn
cd /Users/quinn/planting-monitor || exit 1
ID='-c user.email=quinnneely@gmail.com -c user.name=quinn-Uganda'
LOG=/Users/quinn/planting-monitor/monitor.log

# Branch guard. In Aug 2026 this repo was left checked out on an unrelated branch
# (another session's work), so every push silently failed for days. Bail loudly
# rather than committing planting output onto someone else's branch.
BR=$(/usr/bin/git rev-parse --abbrev-ref HEAD)
if [ "$BR" != "main" ]; then
    echo "$(date) ABORT: repo is on branch '$BR', expected 'main' — not touching it" >> "$LOG"
    exit 1
fi

# 1. pull the cloud's latest first (autostash protects any stray local changes)
/usr/bin/git $ID pull --rebase --autostash -q origin main >> monitor.log 2>&1

# 2. regenerate outputs
/usr/bin/python3 planting_monitor.py >> monitor.log 2>&1
/usr/bin/python3 scorecard.py >> monitor.log 2>&1

# 3. commit + push; if the cloud raced us, pull again and retry once
/usr/bin/git add -A
if ! /usr/bin/git diff --cached --quiet; then
    /usr/bin/git $ID commit -q -m "Daily planting status $(date +%Y-%m-%d) (mac)"
    if /usr/bin/git push -q origin main >> monitor.log 2>&1; then
        echo "$(date) mac run: pushed OK" >> monitor.log
    else
        /usr/bin/git $ID pull --rebase --autostash -q origin main >> monitor.log 2>&1
        /usr/bin/git push -q origin main >> monitor.log 2>&1 \
            && echo "$(date) mac run: pushed OK (after retry)" >> monitor.log \
            || echo "$(date) mac run: PUSH FAILED" >> monitor.log
    fi
fi
