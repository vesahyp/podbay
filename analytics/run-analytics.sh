#!/bin/bash
#
# Nightly refresh of the podbay.tienoo.com traffic rollup (pattern:
# kivikko's analytics/run-analytics.sh).
#   1. clavesa: run the `podbay-traffic` pipeline (Spark, local runner).
#      It reads the CloudFront logs of podbay.tienoo.com, flags bots,
#      rolls up per day and writes data/analytics.json to the site bucket.
#   2. invalidate /data/analytics.json on the distribution.
#
# The rollup outlives the raw logs: those expire after 90 days, and the
# Delta tables plus the published JSON keep every day the pipeline has
# ever seen.
#
# Runs nightly from cron at 07:30, through a wrapper outside this repo.
# Needs Docker for the local Spark runner, and the infra/ stack applied:
# the distribution id comes from its outputs.

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# cron runs with a minimal PATH; Homebrew and /usr/local so clavesa, aws
# and docker resolve by bare name.
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"
export AWS_PROFILE="${AWS_PROFILE:-personal}"
export AWS_REGION="${AWS_REGION:-eu-north-1}"
export CLAVESA_WORKSPACE="$REPO/analytics/clavesa"

LOG_DIR="$REPO/analytics/logs"
LOG="$LOG_DIR/analytics.log"
STATUS="$LOG_DIR/last-run.txt"

mkdir -p "$LOG_DIR"
cd "$REPO" || { echo "$(date -u +%FT%TZ) FATAL: cannot cd $REPO" >> "$LOG"; exit 1; }
# Terraform is pinned in .mise.toml; mise picks the version from here.
DIST="$(mise exec -- terraform -chdir=infra output -raw distribution_id 2>/dev/null)"

# Exit status has to survive the log rotation below, so the failing branch
# sets this instead of exiting: cron mail keys off the exit code.
rc=0

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %z') analytics run starting ==="
  # On a laptop in the morning Docker Desktop may not be up. A legible skip
  # beats a cryptic failure, and the next night catches up: the pipeline
  # reads whole log prefixes, not a delta.
  if ! docker info >/dev/null 2>&1; then
    echo "SKIP: Docker is not running; the clavesa pipeline needs it. Aborting."
    echo "$(date '+%F %T %z')  SKIPPED (docker down)" > "$STATUS"
    exit 0
  fi
  if clavesa pipeline run podbay-traffic; then
    if aws cloudfront create-invalidation --distribution-id "$DIST" --paths '/data/analytics.json' >/dev/null 2>&1; then
      echo "invalidated /data/analytics.json on $DIST"
    else
      echo "warn: invalidation failed (the JSON still refreshes within its 5-min TTL)"
    fi
    echo "=== done OK ==="
    echo "$(date '+%F %T %z')  OK" > "$STATUS"
  else
    echo "=== FAILED (see errors above) ==="
    echo "$(date '+%F %T %z')  FAILED" > "$STATUS"
    rc=1
  fi
} >> "$LOG" 2>&1

# Rotate the log past 10MB, keeping one previous copy.
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG")" -gt 10485760 ]; then
  mv "$LOG" "$LOG.old"
fi

exit "$rc"
