#!/bin/bash
# Daily: regenerate the planting status, then publish to GitHub (Pages + raw CSV).
# Invoked by launchd. Uses absolute paths because launchd has a minimal PATH.
export HOME=/Users/quinn
cd /Users/quinn/planting-monitor || exit 1

/usr/bin/python3 planting_monitor.py >> monitor.log 2>&1
/usr/bin/python3 scorecard.py >> monitor.log 2>&1

/usr/bin/git add -A
if ! /usr/bin/git diff --cached --quiet; then
    /usr/bin/git -c user.email="quinnneely@gmail.com" -c user.name="quinn-Uganda" \
        commit -q -m "Daily planting status $(date +%Y-%m-%d)"
    /usr/bin/git push -q origin main >> monitor.log 2>&1 && \
        echo "$(date) pushed OK" >> monitor.log || \
        echo "$(date) PUSH FAILED" >> monitor.log
fi
