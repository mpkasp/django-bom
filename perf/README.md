# List-page performance measurements

`bom/tests/test_performance.py` seeds a fixed catalog (see `PERF_DATASET` in
`bom/helpers.py`) and records query count, database time, wall time, and
response size for `home`, `report`, and `part_info`.

## Record a step

From the repo root, against SQLite (indicative timings):

```
PERF_STEP=before UPDATE_PERF_BASELINE=1 uv run pytest bom/tests/test_performance.py -s
```

Against Postgres, set the same `SQL_*` environment variables the app uses in
production, then run the same command. Query counts are comparable either way;
wall-clock numbers are only meaningful on Postgres.

`UPDATE_PERF_BASELINE=1` merges the step into `perf/baseline.json`. Without it,
results go only to stdout and `perf/last_run.json` (gitignored).

On GitHub Actions the same table is appended to `$GITHUB_STEP_SUMMARY`.

## End-to-end (Caddy / gunicorn)

Query counts cannot see compression or extra gunicorn workers. Against a running
stack, with a session cookie:

```
BASE_URL=http://localhost COOKIE='sessionid=...' ./perf/measure_e2e.sh
```
