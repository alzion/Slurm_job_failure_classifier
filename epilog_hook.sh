#!/bin/bash
# Slurm epilog hook for the GPU fleet failure classifier.
#
# Install on the Slurm controller:
#   cp epilog_hook.sh /etc/slurm/epilog.d/01_classify.sh
#   chmod +x /etc/slurm/epilog.d/01_classify.sh
#
# Then reload Slurm: scontrol reconfig
#
# Configuration (set as environment variables or edit defaults below):
#   CLASSIFIER_URL  — base URL of the override-api on the monitoring host
#   SACCT_PATH      — where to write the sacct JSON (must match LOG_MOUNT)
#
# This script calls the monitoring host's override-api over HTTP — it does
# NOT require Docker on the Slurm controller.

CLASSIFIER_URL="${CLASSIFIER_URL:-http://monitoring-host:8002}"
SACCT_PATH="${SACCT_PATH:-/shared/logs/sacct_data.json}"
ADAPT_SACCT="${ADAPT_SACCT:-/opt/classifier/adapt_sacct.py}"
LOG_TAG="[gpu-classifier epilog job=${SLURM_JOB_ID}]"

# Step 1: fetch sacct record for this job.
# Retry once with a brief delay — the accounting DB may not have committed
# the job record in the instant the epilog fires.
if ! python3 "$ADAPT_SACCT" --job-id "$SLURM_JOB_ID" --output "$SACCT_PATH" 2>/dev/null; then
    sleep 3
    if ! python3 "$ADAPT_SACCT" --job-id "$SLURM_JOB_ID" --output "$SACCT_PATH"; then
        echo "$LOG_TAG sacct fetch failed — classification skipped" >&2
        exit 0   # never abort the epilog on classifier failure
    fi
fi

# Step 2: trigger classification via HTTP.
# curl -sf: -s suppresses progress, -f returns non-zero on HTTP errors.
# The || true ensures epilog continues even if the monitoring host is unreachable.
if ! curl -sf -m 30 -X POST \
        "$CLASSIFIER_URL/api/v1/classify/$SLURM_JOB_ID" \
        -H "Content-Type: application/json" \
        -o /dev/null; then
    echo "$LOG_TAG classify request failed (monitoring host unreachable?)" >&2
fi

exit 0
