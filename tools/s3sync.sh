#!/usr/bin/env bash
# 8gpu-bench S3 sync (spec 07.7).
#
# Syncs one local directory into the confidential results store.
# Requires:
#   BENCH_S3_URI   store prefix, for example s3://<bucket>/8gpu-bench
#                  (written to ~/.bench/bench.env by the s3-store role)
#   s3cmd          configured (~/.s3cmd, mode 0600, written by the same role)
#
# Usage:
#   s3sync.sh [--dry-run] <local-dir> [dest-suffix]
#
# The destination is $BENCH_S3_URI/<dest-suffix>/. Without dest-suffix
# the basename of <local-dir> is used, so the campaign directory keeps
# its campaign ID as the prefix.
#
# Sync never deletes remote objects (no --delete-removed). Deletion
# belongs to the bucket lifecycle policy, not to the runner.
set -euo pipefail

dry_run=0
if [ "${1:-}" = "--dry-run" ]; then
  dry_run=1
  shift
fi
if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  echo "usage: s3sync.sh [--dry-run] <local-dir> [dest-suffix]" >&2
  exit 2
fi

src="$1"
suffix="${2:-$(basename "${src%/}")}"

if [ ! -d "$src" ]; then
  echo "s3sync: not a directory: $src" >&2
  exit 2
fi
: "${BENCH_S3_URI:?BENCH_S3_URI is not set. Source ~/.bench/bench.env (written by the s3-store role) or export it.}"

dest="${BENCH_S3_URI%/}/${suffix%/}/"

cmd=(s3cmd sync "$src/" "$dest")
if [ "$dry_run" = 1 ]; then
  cmd+=(--dry-run)
fi
echo "+ ${cmd[*]}"
"${cmd[@]}"
