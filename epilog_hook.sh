#!/bin/bash
# Slurm epilog hook for the GPU fleet failure classifier.
#
# Install on the Slurm controller:
#   cp epilog_hook.sh /etc/slurm/epilog.d/01_classify.sh
#   chmod +x /etc/slurm/epilog.d/01_classify.sh
#
# Slurm calls this script after every job completes, with the job's
# environment variables set (SLURM_JOB_ID, SLURM_JOB_NODELIST, etc.).
#
# What it does:
#   1. Fetches sacct --json for this job and writes to SACCT_PATH.
#   2. Invokes the classifier in hook mode for this specific job.
#
# The classifier container must be running; this script calls it via
# `docker exec` by default. Set CLASSIFIER_EXEC to override.

set -euo pipefail

SACCT_PATH="${SACCT_PATH:-/var/log/slurm/sacct_data.json}"
CLASSIFIER_EXEC="${CLASSIFIER_EXEC:-docker exec classifier}"

# Step 1: fetch sacct record for this job
python /opt/classifier/adapt_sacct.py \
    --job-id "$SLURM_JOB_ID" \
    --output "$SACCT_PATH"

# Step 2: classify this job
$CLASSIFIER_EXEC python -m classifier.classifier --job-id "$SLURM_JOB_ID"
