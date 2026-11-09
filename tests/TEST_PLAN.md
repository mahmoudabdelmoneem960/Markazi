# Markazi — Features, Business Logic & Test Map

How to run: `pip install -r requirements-dev.txt` then `python -m pytest` (≈55 s, 363 tests).
Coverage: `python -m pytest --cov=distributor --cov-report=term` (86% overall).

Test types used:
- **Unit**: one function, exact expected values.
- **Rule**: one business rule on a tiny hand-made case.
- **Property**: random scenarios with fixed seeds; checks rules that must hold for any input.
- **Integration**: real Flask app + temporary DB/data folder.
- **Regression**: locks in a bug fixed during this test phase.
- **Security**: authentication, request guards and abuse inputs.
- **Performance**: a smoke test that a large run finishes in reasonable time.

---

## 1. Distribution engine — `distributor/core/engine.py`
| Rule / logic | Where | Tests (`tests/…`) |
|---|---|---|
| R1 Student only goes to a center in their own governorate (`GOV_ALLOW` empty) | `distribute`, `allowed_mask` | `test_engine_rules::test_R1_*`, property `test_invariants_on_random_scenarios` |
| R2 Same area first (`area_key`: ignores order / «حي» / «ال» / hamza), then nearest distance; same-area only counts within max km | greedy `(tier, dist)` sort | `test_R2_*`, property (no better free option left) |
| R3 Exact level + track (L3 ≠ L3WB); level text → key (`level_key`) and group code → key (`group_key`, legacy `L3WB_0001` + new `PH-…`/`ON-…`) must agree | `level_key`, `group_key` | `test_level_key*`, `test_group_key`, `test_level_and_group_key_agree_for_every_track` |
| R4 Max km (inclusive); distance = straight line × detour or OSRM road distance; a missing OSRM route falls back to the estimate | `distribute`, `osrm_matrix` | `test_R4_*`, `test_distribute_osrm_*`, `test_osrm_matrix_*` |
| R5 Capacity never exceeded; one group per student; seats spread evenly across a center's groups | greedy `_greedy` | `test_R5_*`, `test_zero_or_bad_capacity…`, property |
| VP reasons: no coords / unknown level / unknown gov / level exists nowhere / not in gov / too far / all full | `reason[]` | `test_every_vp_reason`, property (a "full" reason must be true) |
| Governorate name resolution (Arabic spellings, «محافظة X», English codes) — **single source `engine.gov_code`** | `gov_code`, `get_gov_band` | `test_gov_code_*`, `test_band_lookup_agrees…` |
| Bands 1/2/3 (9 governorates each); Band-2 alert and «البند» column next to gov | `CITY_BANDS`, summary | `test_bands_cover_all…`, `test_band2_students…` |
| Data-quality checks: NID 14 digits (>1% → alert), duplicate/same-coords centers, lat = lon, student >100 km from governorate peers | `distribute` | `test_nid_quality_alerts`, `test_suspicious_*` |
| Reports: deficit (governorate × level), extra groups = ⌈deficit / most common capacity⌉, load, center load, QGIS lines | `distribute` | `test_deficit_*`, `test_reports_are_consistent…` |
| OSRM disk cache (map fingerprint, reused rows) | `distribute` | `test_distribute_with_osrm_uses_road_distances_and_cache` |
| Cancel via progress callback; deterministic output | | `test_progress_callback_and_cancel`, `test_deterministic_output` |
| Performance: 30k students × 300 centers | | `test_performance_large_sheet` (≈4 s) |

## 2. Location preparation — `core/geoprep.py`, `core/locdb.py`, `core/geocoder.py`, `core/geonorm.py`
| Logic | Tests |
|---|---|
| Student location precedence: manual NID override → sheet coords (auto/sheet mode) → address cache → gazetteer → group override; out-of-Egypt coords ignored | `test_locations` (prepare), `test_api::test_full_run_flow` |
| Generated-looking sheet coords detected (tight cluster ≥300, same address with different coords) | `test_locations`, `test_sheets::test_preflight_*` |
| Center location store: sheet never overwrites `manual` or `google` sources; `moved` when the sheet moves a center >50 m; a manually edited address is kept; a new link clears old link checks | `test_locations` (centers_upsert) |
| Coordinate parsing: `lat, lon`, swapped pair, Arabic digits, DMS, all Google link forms, strict zoom rule | `test_locations` (parse_coords) |
| Google verify decision table (`decide`): fill / same_current / same_link / differs / approx / conflict; a manual location is never changed | `test_locations` |
| Center review status (`review_state`): confirmed → recheck → no_link → link_unreadable/pending → vague → far/ok, with tolerance by address level | `test_locations`, `test_center_review` |

## 3. Upload & preflight — `services/sheets.py`, `services/preflight.py`, `services/groupsheet.py`
| Logic | Tests |
|---|---|
| CSV (UTF-8 / CP1256) and Excel read as text (NID never becomes 3.1E+13) | `test_services_runs::test_parse_file_formats…` |
| «Student DB» export auto-adapted (city→gov, level from old group code first, then level name) | `test_sheets`, `test_services_runs::test_student_db_level…` |
| Preflight: missing columns = error; bad NIDs >1% = error; duplicates / unknown governorate / Band 2 / unknown level = warnings; seats-vs-demand deficit; center location sources | `test_sheets::test_preflight_*`, `test_services_runs::test_preflight_*` |
| Group sources: centers sheet, centers DB + groups sheet, or last run's groups; Arabic day names; day from code A/B; default capacity | `test_groupsheet`, `test_services_runs::test_normalize_groups…`, `test_build_…` |

## 4. Planning — `services/planning.py`, `services/areacheck.py`
| Logic | Tests |
|---|---|
| Totals, governorate / band / level ranking and %, natural level order (L1 < L2 < L10), unknowns, NID stats | `test_planning_logic::test_*` |
| Areas grouped by spelling variants; «بدون منطقة» last | `test_areas_merge…` |
| Groups needed = ⌈students / capacity⌉ per (governorate × level); unknown governorate/level excluded | `test_groups_needed`, `test_total_groups…` |
| Area problems: empty area, street instead of area, area belongs to another governorate (exact / suspected) | `test_areacheck` |
| History, rename, change capacity/mode, delete, flags paging, Excel | `test_planning_logic::test_planning_api_validation`, `test_api` |

## 5. Group codes — `services/groupcodes.py`
| Logic | Tests |
|---|---|
| Online `ON-{A/B}-S1-L{n}-{TRACK}-C###` nationwide; Physical `PH-{A/B}-{GOV3}-V01-…` per governorate; Band 2 and unknown governorates excluded | `test_groupcodes`, `test_planning_logic::test_group_codes_invariants` (16 random cases) |
| Fewest groups, sizes equal ±1, ≤ capacity, numbering C001..Cn continuous, Friday/Saturday 50/50 ±1 per level and overall | property test |
| Generated codes round-trip into the engine (`group_key`) | `test_group_codes_round_trip…` |
| GOV3 from center code prefixes; a code shared by two governorates falls back to the default | `test_gov3_codes_from_centers_table` |

## 6. After a run — `services/runs.py`, `compare.py`, `whatif.py`, `exports.py`
| Logic | Tests |
|---|---|
| Background job: progress, cancel, files (Excel 11 sheets, QGIS zip) | `test_api::test_full_run_flow` |
| Compare: key = NID if ≥95% valid and unique, else row; same/different center, VP↔assigned, by governorate, Excel | `test_services_runs::test_compare_*` |
| What-if: rescued = min(seats, VP of that level+governorate within max km), nearest first; invalid capacity rejected | `test_whatif_*` |
| Per-center Excel (one sheet per center + VP per governorate); Excel-safe, case-insensitive sheet names | `test_per_center_*`, `test_sheet_name_rules` |
| Map points/filters, center details, far students, Band 2 list, invalid NIDs, edit student (+ NID override), search across runs (Arabic digits, group code) | `test_run_student_queries`, `test_update_student…`, `test_search_student…`, `test_api` |

## 7. Security & robustness — `__init__.py`, `auth.py`, `web/api.py`
| Logic | Tests |
|---|---|
| Every `/api/*` endpoint (60+ method/route pairs, discovered automatically) returns 401 without login; pages redirect | `test_security::test_every_api_endpoint_requires_login` |
| Setup only once; password ≥8; non-local Host rejected; cross-site Origin on POST rejected | `test_security`, `test_api` |
| Login `next` only allows local paths (open redirect fixed) | `test_login_wrong_password_and_open_redirect` |
| No path traversal (run id / token / file key whitelist) | `test_no_path_traversal`, `test_run_dir_rejects_traversal` |
| Bad numbers / run params / what-if values → 400, never 500 | `test_bad_numeric…`, `test_run_params_are_validated`, `test_whatif_rejects…` |
| Audit log masks the national ID | `test_audit_log_masks_national_id` |

---

## Bugs found and fixed in this phase
| # | Bug | Impact | Fix | Regression test |
|---|---|---|---|---|
| 1 | Students only tried their 15 nearest centers; if those were full they became VP with the note "all centers full" while seats were free within range | Wrong VP + wrong reason | Second greedy pass over the remaining in-range centers (`engine.py`) | `test_regression_top_centers_cutoff…`, property test |
| 2 | `City` with spaces (`"cairo "`) → seats_in_gov = 0 in the deficit report | Wrong deficit / extra groups | strip in `seats_g` | `test_regression_city_with_spaces…` |
| 3 | Governorate resolved 4 different ways: «Cairo», «محافظة القاهرة», «بور سعيد», «اسكندرية» became VP "unknown governorate"; geoprep used another table that missed «مرسى مطروح» / «جنوب سينا» | Valid students lost | `engine.gov_code()` used by engine, preflight, planning, geoprep, bands | `test_gov_code_*`, `test_regression_english_and_prefixed…` |
| 4 | Level written as a code (`L3WB`, `L3CY`) lost its track → `L3`; «AI» not mapped to the DT track | Wrong group or VP | `level_key` accepts codes and `AI` | `test_level_key` |
| 5 | Per-center Excel crashed when the governorate column header was «المحافظة»; center codes that differ only in letter case crashed Excel | Export 500 | detect gov column; case-insensitive names | `test_regression_per_center_export…` |
| 6 | What-if accepted capacity ≤ 0 (negative "rescued") | Wrong numbers | validation | `test_whatif_*` |
| 7 | Login `?next=https://evil…` redirected off-site | Open redirect | `_safe_next` | `test_login_…open_redirect` |
| 8 | `page=abc` etc. → HTTP 500; invalid `max_km` → misleading "draft expired" | Poor errors | `_arg_int`, `_run_params` | `test_bad_numeric…`, `test_run_params…` |
| 9 | Google verify sent `monufia` in English (key spelled `menoufia`) | Worse Google matches for Monufia | added the key | `test_locations` |
| 10 | Link `?q=30.04,+31.23` (encoded space) not parsed | Center location not read | regex accepts `+` | `test_locations` |
| 11 | Geocoder: an address ending in its own area name («… - مدينة نصر») matched a *different* neighbourhood with the same word («النصر», 7 km away), then searched for the street there | Students placed 5–7.5 km off, labelled "street" | skip same-name places >2 km from a district-level area | `test_runs_new_e2e::test_geocoder_accuracy_on_known_addresses` |
| 12 | Geocoder: area «حي مدينة نصر» resolved to «النصر» | Wrong anchor for the whole address | full-name match after dropping «حي/قسم/منطقة»; prefer names sharing more words | same |
| 13 | Geocoder: one generic word accepted as a street («شارع مسجد البسيوني» → «شارع المسجد», up to 8 km off) | Wrong street location | refuse one-word institution names | `test_geocoder_does_not_accept_one_generic_word…` |
| 14 | Preflight counted seats of groups with invalid codes (15 shown, engine uses 10) | Over-stated seats | count valid groups only | `test_runs_new_e2e::test_preflight_reports…` |

Address-cache key now includes `geocoder.VERSION`, so locations cached by the old logic are recomputed on the next run.

## Known issue, not fixed (needs a decision)
- **`geonorm.tokens`: «عبد الله» ≠ «عبدالله».** «ال» is stripped before the join, so addresses with these two spellings don't match each other. Fixing it changes the gazetteer keys, so `data/gazetteer.sqlite` would have to be rebuilt (`tools/build_gazetteer.py`) and the address cache would be refreshed. Tracked as a strict `xfail` in `test_locations::test_tokens_join_matches_written_together`.

## Not covered by automated tests
- Front-end JavaScript (maps, pages): needs manual or browser (Playwright) testing.
- Real OSRM server and real Google API: covered with fakes; `tools/test_osrm_egypt.py` checks a live OSRM.
- Geocoding accuracy on real data: the gazetteer tests run only when `data/gazetteer.sqlite` exists (it does on this machine).
