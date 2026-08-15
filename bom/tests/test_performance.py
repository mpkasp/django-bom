import json
import os
import statistics
import time
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from bom.helpers import PERF_DATASET, create_performance_dataset

REPO_ROOT = Path(__file__).resolve().parents[2]
PERF_DIR = REPO_ROOT / "perf"
BASELINE_PATH = PERF_DIR / "baseline.json"
LAST_RUN_PATH = PERF_DIR / "last_run.json"
PAGES = ("home", "report", "part_info")
ITERATIONS = 3

# Tightened after Stage A measurements. Until then these only catch runaway
# regressions (thousands of extra queries), not the optimizations themselves.
MAX_QUERIES = {
    "home": 100000,
    "report": 100000,
    "part_info": 100000,
}


def _median_record(samples):
    def median(key):
        return statistics.median(sample[key] for sample in samples)

    return {
        "queries": int(median("queries")),
        "db_seconds": round(median("db_seconds"), 6),
        "wall_seconds": round(median("wall_seconds"), 6),
        "response_bytes": int(median("response_bytes")),
    }


def _markdown_table(pages):
    lines = [
        "| page | queries | db_seconds | wall_seconds | response_bytes |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name in PAGES:
        row = pages[name]
        lines.append(
            f"| {name} | {row['queries']} | {row['db_seconds']:.4f} | "
            f"{row['wall_seconds']:.4f} | {row['response_bytes']} |"
        )
    return "\n".join(lines)


def _write_results(step_id, pages, params):
    payload = {
        "step": step_id,
        "dataset": params,
        "pages": pages,
    }
    PERF_DIR.mkdir(exist_ok=True)
    LAST_RUN_PATH.write_text(json.dumps(payload, indent=2) + "\n")

    if os.environ.get("UPDATE_PERF_BASELINE") != "1":
        return

    if BASELINE_PATH.exists():
        baseline = json.loads(BASELINE_PATH.read_text())
    else:
        baseline = {"dataset": params, "steps": []}
    baseline["dataset"] = params
    steps = [step for step in baseline.get("steps", []) if step.get("id") != step_id]
    steps.append({"id": step_id, "pages": pages})
    baseline["steps"] = steps
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")


def _append_github_summary(step_id, pages):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write(f"\n## List-page performance (`{step_id}`)\n\n")
        handle.write(_markdown_table(pages))
        handle.write("\n")


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class ListPagePerformanceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        dataset = create_performance_dataset()
        cls.user = dataset["user"]
        cls.organization = dataset["organization"]
        cls.assembly_part = dataset["assembly_part"]
        cls.params = dataset["params"]

    def setUp(self):
        self.client = Client()
        self.client.login(username="perftest", password="perfpassword")

    def _measure(self, url):
        connection.queries_log.clear()
        with CaptureQueriesContext(connection) as ctx:
            started = time.perf_counter()
            response = self.client.get(url)
            elapsed = time.perf_counter() - started
            captured = list(ctx.captured_queries)
        self.assertEqual(response.status_code, 200)
        return {
            "queries": len(captured),
            "db_seconds": round(
                sum(float(query.get("time") or 0) for query in captured), 6
            ),
            "wall_seconds": round(elapsed, 6),
            "response_bytes": len(response.content),
        }

    def _measure_page(self, url):
        self._measure(url)
        samples = [self._measure(url) for _ in range(ITERATIONS)]
        return _median_record(samples)

    def test_list_pages_query_budget(self):
        pages = {
            "home": self._measure_page(reverse("bom:home")),
            "report": self._measure_page(reverse("bom:report")),
            "part_info": self._measure_page(
                reverse("bom:part-info", kwargs={"part_id": self.assembly_part.id})
            ),
        }
        step_id = os.environ.get("PERF_STEP", "current")
        _write_results(step_id, pages, self.params)
        _append_github_summary(step_id, pages)
        print(f"\nperformance step={step_id}\n{_markdown_table(pages)}\n")

        for name in PAGES:
            self.assertLessEqual(
                pages[name]["queries"],
                MAX_QUERIES[name],
                f"{name} issued {pages[name]['queries']} queries "
                f"(limit {MAX_QUERIES[name]})",
            )

    def test_dataset_matches_expected_shape(self):
        from bom.models import Part, PartRevision

        self.assertEqual(Part.objects.filter(organization=self.organization).count(), PERF_DATASET["n_parts"])
        self.assertEqual(
            PartRevision.objects.filter(part__organization=self.organization).count(),
            PERF_DATASET["n_parts"] * PERF_DATASET["revisions_per_part"],
        )
