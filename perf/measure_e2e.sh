#!/usr/bin/env bash
# Time list pages against a running stack (Caddy + gunicorn + Postgres).
# Usage: BASE_URL=http://localhost COOKIE='sessionid=...' ./perf/measure_e2e.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost}"
COOKIE="${COOKIE:-}"
RUNS="${RUNS:-10}"
PAGES="${PAGES:-/ /report/}"

if [[ -z "${COOKIE}" ]]; then
  echo "Set COOKIE to an authenticated sessionid cookie." >&2
  exit 1
fi

measure_page() {
  local path="$1"
  local tmp
  tmp="$(mktemp)"
  for _ in $(seq 1 "${RUNS}"); do
    curl -sS -o /dev/null -b "${COOKIE}" -w '%{time_starttransfer} %{time_total} %{size_download}\n' \
      "${BASE_URL}${path}"
  done > "${tmp}"
  python3 - "${path}" "${tmp}" <<'PY'
import statistics, sys
path, filename = sys.argv[1], sys.argv[2]
ttfb, total, size = [], [], []
with open(filename) as handle:
    for line in handle:
        a, b, c = line.split()
        ttfb.append(float(a))
        total.append(float(b))
        size.append(int(float(c)))
print(f"{path}\tmedian_ttfb={statistics.median(ttfb):.4f}s\tmedian_total={statistics.median(total):.4f}s\tbytes={int(statistics.median(size))}")
PY
  rm -f "${tmp}"
}

echo "Sequential (n=${RUNS})"
for path in ${PAGES}; do
  measure_page "${path}"
done

echo
echo "Concurrent (4 requests to /)"
seq 1 4 | xargs -P 4 -I{} curl -sS -o /dev/null -b "${COOKIE}" -w '%{time_starttransfer} %{time_total}\n' "${BASE_URL}/"
