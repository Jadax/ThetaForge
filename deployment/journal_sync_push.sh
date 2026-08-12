#!/bin/bash
# Regenerates journal/trades.json from the VM's live paper-order ledger and
# publishes it to the actually-live site, so an autonomously placed trade
# appears on the public journal without a human running
# scripts/add_trade.py / sync_journal.py by hand.
#
# GitHub Pages for this repo serves from the gh-pages branch root (confirmed
# via its file layout matching the live URLs with no /journal/ prefix), not
# from main -- and there is no existing automation that mirrors journal/ on
# main into gh-pages. Pushing only to main would update history but never
# actually appear on https://journal.astraiva.app/ (custom domain via the
# CNAME file on gh-pages; the github.io URL still resolves too). This script
# does both: commits the regenerated journal to main (preserving that as the
# source-of-truth history, same as the existing manual workflow), then
# mirrors the same files into a separate gh-pages checkout and pushes that
# too, since that's what is actually served.
#
# Reuses the existing, already-tested scripts/sync_journal.py unchanged --
# just points --ledger at this VM's actual Bridge ledger instead of the
# default repo-relative path, since the Bridge runs here now, not in a
# local checkout next to this clone.
set -euo pipefail

MAIN_REPO="/opt/thetaforge-bridge/ThetaForge"
PAGES_REPO="/opt/thetaforge-bridge/ThetaForge-gh-pages"
LEDGER="/opt/thetaforge-bridge/data/paper_order_ledger.json"
export GIT_SSH_COMMAND="ssh -i /home/ubuntu/.ssh/thetaforge_repo_deploy_key -o IdentitiesOnly=yes"

cd "$MAIN_REPO"
git pull --ff-only origin main

python3 scripts/sync_journal.py --ledger "$LEDGER"

if git diff --quiet -- journal/trades.json; then
    echo "journal unchanged, nothing to publish"
    exit 0
fi

git add journal/trades.json
git commit -m "Auto-sync journal from VM paper-order ledger ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
git push origin main

cd "$PAGES_REPO"
git pull --ff-only origin gh-pages
cp "$MAIN_REPO"/journal/.nojekyll "$MAIN_REPO"/journal/*.js "$MAIN_REPO"/journal/*.css "$MAIN_REPO"/journal/*.svg "$MAIN_REPO"/journal/*.json "$MAIN_REPO"/journal/index.html "$PAGES_REPO"/
mkdir -p "$PAGES_REPO"/learn
cp "$MAIN_REPO"/journal/learn/index.html "$PAGES_REPO"/learn/index.html

if git diff --quiet && git diff --cached --quiet; then
    echo "gh-pages already matches journal/, nothing to push there"
    exit 0
fi

git add -A
git commit -m "Publish journal update ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
git push origin gh-pages
echo "journal published to https://journal.astraiva.app/"
