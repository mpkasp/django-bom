# Material List Performance Improvement Plan (Fixed)

Revised plan incorporating codebase review (August 2026). Supersedes gaps and stale items in [material-list-performance-plan.md](material-list-performance-plan.md).

## Objective

Reduce the time required to navigate between pages of the material list rendered by `home()` in `bom/views/views.py`. Pagination currently takes more than five seconds in production.

Target outcomes:

- Page navigation under 500 ms where practical, and under one second at normal production load.
- Search and material filters under one second where practical.
- No N+1 queries.
- No behavior changes to latest-revision selection, seller selection, pricing, search, print, export, authorization, or organization isolation.

## Current State vs Baseline

| Metric | Before Stage A | After Stage A | Plan target |
| --- | ---: | ---: | ---: |
| Home page queries (SQLite, 80-part dataset) | 1,015 | 110 | < 15 |
| Perf test query ceiling | — | 200 | Tighten after fixes |
| Page-2 measurement | — | Not measured | Required |
| PostgreSQL wall-time SLA | — | Not measured | < 1 s page 1/2 |

Source: `perf/baseline.json`, `bom/tests/test_performance.py`.

Production dump (~480 parts, ~594 revisions) is too small to explain 5 s pagination alone. Remaining latency is likely query shape, a latest-revision subquery bug, recursive BOM cost on assembly rows, expensive `COUNT(*)`, PostgreSQL plans, or infrastructure latency.

## Already Implemented (Stage A — Seller Prefetch)

The following work is **done** on `main` via `bom/list_queries.py` and the `stage-a` baseline:

- `paginate_part_revs()` applies `select_related` for part, organization, number class, and primary manufacturer part.
- `prepare_part_revs_for_list_page()` prefetches manufacturer parts → seller parts → sellers for the current page only.
- Optimal seller is computed once per distinct part on the page and cached on the part instance via `_optimal_seller_result` / `_optimal_seller_qty`.
- Print/export uses `prepare_all_part_revs_for_list_page()` and `attach_print_unit_costs()` for raw materials.

Home queries dropped from ~1,015 to ~110. **Do not re-implement seller N+1 elimination** — remaining seller work is explicit row attributes and regression tests (see Stage C).

## Known Bugs (Fix Before Other Optimizations)

### 1. Latest-revision subquery returns all revisions, not one per part

Current code in `home()` (`bom/views/views.py`):

```python
PartRevision.objects.filter(part_id__in=part_ids)
    .annotate(max_id=Max("id"))
    .values("id")
```

Django groups by revision `id`, so every revision row passes through the subquery instead of one latest revision per part.

Canonical pattern already used in `Customer.latest_prices()` (`bom/models.py`):

```python
.values("part_id")
.annotate(max_id=Max("id"))
.values("max_id")
```

**Impact:** inflated row count (~594 vs ~480), heavier `Paginator` count, more page/export work.

**Action:** add a correctness test (one revision per part), then fix all occurrences in `home()` and related views.

### 2. Search rebuild drops `product` filter

When `query` is present, `home()` rebuilds `part_revs` (lines 240–248) but does not re-apply the `product=0/1` filter from lines 204–208. `/?product=0&q=...` likely ignores raw-material filtering.

**Action:** unify query construction so all filters apply in one pipeline; add regression test for search + product combinations.

## Current Hot Paths (Remaining)

### Query construction

- Eager `part_ids = list(parts.values_list("id", flat=True))` materializes all IDs in Python (lines 191, 238).
- Latest-revision queryset is built twice: before and after search.
- No deterministic ordering tie-breaker (`part_id` or `part_revision_id`).

### Pagination count

`Paginator` issues `COUNT(*)` over the full latest-revision queryset. The UI (`bom/templates/bom/ui/pagination.html`) shows page numbers only — no "Showing X of Y" — but Django still runs the count internally.

### Seller display (clarity, not N+1)

The template calls `part.optimal_seller` again after prefetch. This is a **cached Python re-call**, not extra SQL when prefetch succeeded. Remaining work: attach explicit `part_rev.list_seller` and test that the fallback DB path never runs during list rendering.

### BOM unit cost (likely main remaining latency driver)

The template renders `part_rev.bom_unit_cost`. For assemblies this calls `self.indented().bom_unit_cost`, recursively walking the BOM. Cached per revision, but expensive on first access for each assembly row on the page.

Print path partially mitigates via `attach_print_unit_costs()`; the normal paginated list path does not.

### Autocomplete (disabled by default)

When `enable_autocomplete=True`, `home()` iterates all matching revisions and manufacturer parts and embeds the full dictionary in every HTML response before pagination. Keep disabled; move to a separate endpoint if needed.

## Phase 1: Establish a Production-Like Baseline

1. Measure separately:

   - `/`
   - `/?page=2`
   - `/?page=10`
   - `/?product=0`
   - `/?product=1`
   - `/?q=<common-term>`
   - `/?page=2&q=<common-term>`
   - `/?product=0&q=<common-term>` (validates product-filter bug fix)

2. Record total wall time, database time, query count, response size, and database CPU/load.

3. Add temporary timing instrumentation around:

   - `PartClassSelectionForm`
   - base `parts` queryset construction
   - search filtering
   - latest-revision queryset construction
   - autocomplete generation
   - `paginate_part_revs`
   - seller prefetch and optimal-seller calculation
   - `bom_unit_cost` / `indented()` per row
   - template rendering

4. Capture SQL from the slowest requests and run `EXPLAIN (ANALYZE, BUFFERS)` on PostgreSQL.

5. Measure paginator count separately. Determine whether time is spent in count, page query, prefetch, BOM cost, Python, or template rendering.

6. Extend `bom/tests/test_performance.py` and `perf/measure_e2e.sh` beyond page 1. Add a dataset closer to the production dump (~480 parts).

Do not add indexes or rewrite queries based only on assumptions. Keep before/after plans and timings.

## Phase 2: Fix Latest-Revision Subquery and Query Construction

### 0. Fix latest-revision subquery (first)

Replace broken subquery with the `Customer.latest_prices()` pattern. Verify with tests that exactly one revision per part is returned.

### 1. Keep part IDs lazy

Replace:

```python
part_ids = list(parts.values_list("id", flat=True))
```

with a subquery:

```python
part_ids = parts.values("id")
```

### 2. Apply filters before selecting latest revisions

Refactor `home()` into one query-building path:

1. Resolve organization and form state.
2. Create `Part.objects.filter(organization=organization)`.
3. Apply part-class filtering.
4. Apply search filtering.
5. Select the latest revision for the resulting parts.
6. Apply `product=1` or `product=0` material filtering.
7. Apply stable ordering.
8. Handle download, print, or pagination.

Do not build one latest-revision query and then rebuild it after search.

### 3. Preserve and document latest-revision semantics

The intended rule is greatest `PartRevision.id` per part. Verify this is the domain rule before changing it. If timestamp is the true rule, use `timestamp DESC, id DESC` and update tests deliberately.

### 4. Add a unique ordering tie-breaker

```python
.order_by(
    "part__number_class__code",
    "part__number_item",
    "part__number_variation",
    "part_id",
)
```

### 5. Investigate the count query

First optimize the underlying queryset (subquery fix alone may help significantly). If count remains costly, evaluate:

- A count-friendly queryset.
- Counting matching parts rather than materialized revision rows where equivalent.
- Short-lived organization/filter-specific count caching.
- Removing exact totals from the UI in favor of next/previous navigation, only if product requirements allow it (low UX impact today since totals are not displayed).

## Phase 3: Page-Row Preparation (Seller + Unit Cost)

### Seller (explicit attributes)

1. Inspect the `Prefetch` graph in `bom/list_queries.py` — already covers seller/manufacturer access for the dashboard.
2. Attach prepared values on each row:

```python
part_rev.list_seller = seller
```

3. Update `dashboard.html`:

```django
{% with part=part_rev.part seller=part_rev.list_seller %}
```

4. Add query-capture tests proving page 1 and page 2 do not issue per-row seller queries and never hit `Part.optimal_seller()` fallback DB path.

Do not move seller selection into SQL unless profiling proves Python selection is still expensive after prefetch. The dump has only 453 seller parts.

### BOM unit cost (elevated priority)

1. Profile pages with only raw materials, only assemblies, and mixed rows.
2. Confirm whether `bom_unit_cost` causes additional queries during list rendering.
3. Ensure nested BOM traversal uses prefetched data where possible.
4. Attach prepared display value on each row:

```python
part_rev.list_unit_cost = ...
```

5. If recursive computation remains the bottleneck:

   - Short term: compute once per row in `prepare_part_revs_for_list_page()`.
   - Medium term: bulk-prefetch the BOM graph.
   - Long term: denormalized calculated cost per revision/currency with invalidation on pricing/BOM changes.

6. Preserve raw-material seller cost vs assembly BOM cost distinction. Add correctness tests before denormalizing.

## Phase 4: Remove Full-Catalog Autocomplete Work

Keep `enable_autocomplete=False` by default.

If autocomplete is required:

1. Add a separate authenticated JSON endpoint, e.g. `/search-autocomplete/?q=...`.
2. Scope every query to the current organization.
3. Require at least two or three characters.
4. Return only 10–20 results.
5. Debounce browser requests.
6. Use `select_related` and `values()`/`values_list()` for a small response.
7. Cache common results briefly with organization-scoped keys.
8. Remove full dictionary generation and JSON embedding from the normal dashboard request.

## Phase 5: Add Indexes Based on PostgreSQL Plans

Evaluate candidates only after capturing query plans:

```text
bom_partrevision (part_id, id DESC)
bom_partrevision (part_id, material, id DESC)
bom_part (organization_id, number_class_id, number_item, number_variation, id)
bom_sellerpart (manufacturer_part_id, minimum_order_quantity)
```

For `icontains` search, evaluate `pg_trgm` plus GIN/GiST indexes on:

- `bom_partrevision.searchable_synopsis`
- primary manufacturer-part number
- manufacturer name, if needed

For every index: capture before plan, add migration, run `ANALYZE`, capture after plan, compare execution time and buffers. Do not add speculative indexes.

## Phase 6: Reduce Row and Template Work Carefully

After query-count tests exist, evaluate `only()` or `defer()` for fields not needed by the table and its properties. Deferred fields accessed by model properties can create hidden queries.

Prepare transient list-row values where useful:

- `list_part_number`
- `list_seller`
- `list_unit_cost`
- `list_category_name`

Do not duplicate business logic without tests.

## Phase 7: Evaluate Pagination and Caching

### Offset pagination

Measure pages 1, 2, 10, and 50. Five seconds on page 2 with ~480 parts is unlikely from offset alone. Implement keyset pagination only if latency grows substantially with page number.

### Current revision modeling

At larger scale, consider a current-revision relationship on `Part` or a read-model table. Not the first optimization for the current dump size.

### Caching

Cache only safe, organization-scoped data (part-class choices, autocomplete results, short-lived counts). Full-page cache must account for organization, role, language, currency, CSRF, and catalog-version invalidation. Add caching only after query and N+1 problems are fixed.

## Tests and Acceptance Criteria

Add or update tests for:

- one latest revision per part (subquery correctness)
- page 1, page 2, and stable ordering
- part-class, product, and raw-material filters
- search + product filter combinations
- all supported search sources
- empty results
- seller and unit-cost display
- print and CSV/XLSX export
- admin-only controls and deletion behavior
- multi-organization isolation
- no per-row queries on page 1 and page 2

Extend `bom/tests/test_performance.py` with a production-like dataset. Run against PostgreSQL as well as SQLite; only PostgreSQL timings represent production.

After optimization, target:

- fewer than 15 queries for a normal paginated home page
- query count independent of page row count
- no query count growth from seller or BOM depth
- page 1 and page 2 under one second in production-like PostgreSQL conditions
- pagination materially below the current five-second behavior
- unchanged search, filtering, cost, print, export, authorization, and tenancy behavior

## Recommended Delivery Order

### Stage A: Measure — partial

Done: baseline JSON, perf test, README, e2e script, Stage A seller prefetch baseline.

Remaining: page-2/filter/search measurements, view instrumentation, PostgreSQL `EXPLAIN` artifacts, larger dataset.

### Stage B0: Fix latest-revision subquery

1. Add correctness test.
2. Fix subquery using `.values("part_id").annotate(max_id=Max("id")).values("max_id")`.
3. Re-measure row count and count-query cost.

### Stage B: Fix query construction

1. Remove eager `part_ids` materialization.
2. Apply all part filters before latest-revision selection in one path.
3. Re-apply `product` filter after search (or eliminate duplicate path).
4. Add deterministic ordering tie-breaker.
5. Add query-count regression tests.

### Stage C: Fix page-row preparation

1. Attach and render `list_seller` explicitly.
2. Profile and attach `list_unit_cost` for assembly rows.
3. Verify no seller N+1 and no BOM-cost query explosion.
4. Add page-1/page-2 query regression tests.

### Stage D: Isolate optional work

1. Keep autocomplete off by default.
2. Move autocomplete to a limited endpoint if needed.
3. Remove full-catalog serialization from the dashboard path.

### Stage E: Optimize PostgreSQL

1. Add only plan-supported indexes.
2. Add trigram search indexes only if search plans justify them.
3. Re-run production-like benchmarks.

### Stage F: Address remaining bottlenecks

1. Optimize or denormalize BOM costs if proven necessary.
2. Consider current-revision modeling at larger scale.
3. Consider keyset pagination or safe caching only when measurements justify them.

## Implementation Constraints

- Do not change the meaning of latest revision without confirming the domain rule.
- Do not use PostgreSQL-only ORM features unless PostgreSQL is the supported production database.
- Do not cache data without organization and permission isolation.
- Do not denormalize costs before proving recursive calculation is a bottleneck.
- Do not optimize only for the 480-part dump; preserve scalability.
- Do not trust template caching implicitly; make prepared row values explicit.
- Do not claim success from SQLite-only timings.
- Fix known bugs (subquery, search+product) before indexes or caching.

## Relevant Files

- `bom/views/views.py` — `home()` query construction
- `bom/list_queries.py` — pagination, seller prefetch, print unit cost
- `bom/models.py` — `PartRevision.bom_unit_cost`, `Customer.latest_prices()` pattern
- `bom/templates/bom/dashboard.html` — list rendering
- `bom/templates/bom/ui/pagination.html` — pagination UI
- `bom/tests/test_performance.py` — query-count regression
- `bom/tests/test_bom.py` — behavioral tests
- `perf/baseline.json` — recorded baselines
- `perf/README.md` — how to run benchmarks
