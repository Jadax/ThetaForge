#!/bin/bash
# Log GCP Advisor memory/swap every 5 minutes to a rolling file.
# Run via crontab: */5 * * * * /opt/thetaforge/monitor_memory.sh
LOG=/var/log/thetaforge/memory_monitor.log
TS=$(date -u +%FT%TZ)
read -r _ TOTAL USED FREE _ _ AVAIL <<< "$(free -m | awk 'NR==2 {print $1,$2,$3,$4,$5,$6,$7}')"
read -r _ SW_TOTAL SW_USED <<< "$(free -m | awk 'NR==3 {print $1,$2,$3}')"
ADV_RSS=$(ps -o rss= -p $(pgrep -f 'uvicorn orchestrator' | head -1) 2>/dev/null | tr -d ' ')
echo "$TS mem_total=${TOTAL}MB mem_avail=${AVAIL}MB swap_total=${SW_TOTAL}MB swap_used=${SW_USED}MB advisor_rss=${ADV_RSS}KB" >> "$LOG"
# rotate at 2000 lines
tail -2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
chmod 664 "$LOG"