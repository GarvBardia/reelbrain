"""scripts/daily_runner.py — the priority/budget orchestration. All mocked:
every Step is injected, so nothing here touches Notion, Gemini, or the clock."""
import logging

from scripts import daily_runner as dr


def _step(name, pending=5, result=None, free=False, local=False, cost=1, calls=None):
    """A Step whose run() records that it ran and returns a canned result.
    `calls` records (name, num_rows, dry_run, deadline) per invocation."""
    calls = calls if calls is not None else []
    return dr.Step(
        name=name, free=free, local=local, cost_per_row=cost,
        find_pending=lambda: [{"i": i} for i in range(pending)],
        run=lambda rows, dry, deadline: (calls.append((name, len(rows), dry, deadline))
                               or (result if result is not None else {"written": len(rows)})),
    ), calls


# --- priority order ------------------------------------------------------------------

def test_steps_run_in_priority_order():
    order = []
    steps = []
    for name in ("named_entities", "recover", "suggested", "plain"):
        s, _ = _step(name, pending=1)
        s.run = (lambda n: lambda rows, dry, deadline: (order.append(n) or {"written": 1}))(name)
        steps.append(s)
    dr.run_day(steps, budget=100, print_fn=lambda m: None)
    assert order == ["named_entities", "recover", "suggested", "plain"]


def test_highest_priority_step_gets_the_budget_first():
    """named_entities is priority 1 precisely because everything else is
    blocked on it — it must consume the budget before anyone else."""
    first, first_calls = _step("named_entities", pending=50, result={"written": 20})
    second, second_calls = _step("recover", pending=50)
    dr.run_day([first, second], budget=20, print_fn=lambda m: None)
    assert first_calls[0][1] == 20        # offered exactly the budget
    assert second_calls == []             # nothing left for the next step


def test_lower_priority_runs_when_budget_remains():
    first, first_calls = _step("named_entities", pending=2, result={"written": 2})
    second, second_calls = _step("recover", pending=5, cost=1, result={"recovered": 3})
    dr.run_day([first, second], budget=20, print_fn=lambda m: None)
    assert first_calls[0][1] == 2
    assert second_calls[0][1] == 5        # 18 budget left, all 5 rows offered


# --- budget slicing ------------------------------------------------------------------

def test_row_slice_respects_per_row_cost():
    """A recovery row costs ~3 calls (extraction + research per entity), so a
    20-call budget must offer ~6 rows, not 20."""
    step, calls = _step("recover", pending=100, cost=3, result={"recovered": 0})
    dr.run_day([step], budget=20, print_fn=lambda m: None)
    assert calls[0][1] == 6               # 20 // 3


def test_budget_decrements_by_rows_actually_acted_on():
    """Spend is counted from what the step reports DOING, not from what it was
    offered — a step handed 20 rows that only wrote 3 leaves 17 for others."""
    first, _ = _step("a", pending=20, result={"written": 3})
    second, second_calls = _step("b", pending=20, result={"written": 0})
    summary = dr.run_day([first, second], budget=20, print_fn=lambda m: None)
    assert summary["used"] == 3
    assert second_calls[0][1] == 17


def test_errors_and_none_found_still_count_as_quota_spent():
    """A Gemini call that errored or returned nothing still consumed quota."""
    step, _ = _step("a", pending=10, result={"written": 1, "none_found": 2, "errors": 1})
    summary = dr.run_day([step], budget=20, print_fn=lambda m: None)
    assert summary["used"] == 4


def test_step_with_no_pending_work_is_skipped_without_spending():
    step, calls = _step("a", pending=0)
    summary = dr.run_day([step], budget=20, print_fn=lambda m: None)
    assert calls == [] and summary["used"] == 0
    assert step.note == "nothing pending"


# --- the free step -------------------------------------------------------------------

def test_free_step_is_never_budgeted():
    free, free_calls = _step("enforce_topics", pending=200, free=True, result={"fixed": 200})
    summary = dr.run_day([free], budget=20, print_fn=lambda m: None)
    assert free_calls[0][1] == 200        # ALL rows, not sliced to budget
    assert summary["used"] == 0           # and costs nothing


def test_free_step_runs_even_after_quota_is_exhausted():
    """The whole point: on a quota-dead day this is the one thing that still
    makes progress."""
    burner, _ = _step("named_entities", pending=5, result={"written": 1, "quota_stopped": True})
    free, free_calls = _step("enforce_topics", pending=7, free=True, result={"fixed": 7})
    summary = dr.run_day([burner, free], budget=20, print_fn=lambda m: None)
    assert summary["quota_stopped"] is True
    assert free_calls[0][1] == 7          # ran anyway


def test_paid_steps_after_a_quota_stop_are_skipped():
    burner, _ = _step("named_entities", pending=5, result={"written": 1, "quota_stopped": True})
    later, later_calls = _step("plain_summary", pending=5)
    dr.run_day([burner, later], budget=20, print_fn=lambda m: None)
    assert later_calls == []
    assert "quota already exhausted" in later.note


# --- local (Ollama-routed) steps -- PROGRESS.md 2026-08-16 -----------------------------

def test_local_step_is_never_budgeted_from_gemini():
    local, local_calls = _step("suggested_action", pending=50, local=True, result={"written": 50})
    summary = dr.run_day([local], budget=5, print_fn=lambda m: None)
    assert local_calls[0][1] == 50        # ALL rows, not sliced to Gemini budget
    assert summary["used"] == 0           # and costs nothing from it


def test_local_step_runs_even_after_gemini_quota_is_exhausted():
    """The whole point: local is a genuinely separate resource, so a
    Gemini-dead day still makes progress on local-routed backlogs."""
    burner, _ = _step("named_entities", pending=5, result={"written": 1, "quota_stopped": True})
    local, local_calls = _step("suggested_action", pending=7, local=True, result={"written": 7})
    summary = dr.run_day([burner, local], budget=20, print_fn=lambda m: None)
    assert summary["quota_stopped"] is True
    assert local_calls[0][1] == 7          # ran anyway


def test_local_step_receives_the_deadline():
    local, local_calls = _step("suggested_action", pending=3, local=True)
    dr.run_day([local], budget=20, local_time_budget_seconds=900, print_fn=lambda m: None)
    assert local_calls[0][3] is not None   # a real deadline, not None


def test_local_step_gets_no_deadline_on_dry_run():
    local, local_calls = _step("suggested_action", pending=3, local=True)
    dr.run_day([local], budget=20, dry_run=True, print_fn=lambda m: None)
    assert local_calls[0][3] is None


def test_local_time_budget_exhausted_skips_remaining_local_steps():
    """Once the wall-clock local budget is spent, further local steps are
    skipped (resumable next run) -- they are NOT gated on Gemini quota, but
    they ARE gated on their own separate time budget."""
    first, first_calls = _step("suggested_action", pending=1, local=True)
    second, second_calls = _step("plain_summary", pending=1, local=True)
    # A budget of 0 seconds means the deadline is already in the past by the
    # time the second local step is reached.
    summary = dr.run_day([first, second], budget=20, local_time_budget_seconds=0,
                          print_fn=lambda m: None)
    assert first_calls == []      # even the first is already past deadline
    assert second_calls == []
    assert "local time budget" in second.note
    assert summary["quota_stopped"] is False  # this is NOT a quota stop


def test_local_step_processed_count_uses_spent_keys_not_full_pending():
    """`processed` (and the log's Local: N figure) reflects rows the step
    actually reports acting on, not the full pending count -- matters once a
    local step's own deadline check stops it partway through its list."""
    local, _ = _step("suggested_action", pending=10, local=True,
                      result={"written": 4, "errors": 1})
    summary = dr.run_day([local], budget=20, print_fn=lambda m: None)
    assert local.processed == 5          # written + errors, not the 10 pending
    assert summary["local_processed"] == 5


def test_local_step_dry_run_reports_full_pending_as_processed():
    local, _ = _step("suggested_action", pending=6, local=True)
    dr.run_day([local], budget=20, dry_run=True, print_fn=lambda m: None)
    assert local.processed == 6


def test_local_processed_excluded_from_gemini_used_count():
    local, _ = _step("suggested_action", pending=10, local=True, result={"written": 10})
    summary = dr.run_day([local], budget=20, print_fn=lambda m: None)
    assert summary["used"] == 0
    assert summary["local_processed"] == 10


def test_format_log_paragraph_shows_gemini_and_local_split():
    gemini_step, _ = _step("named_entities", pending=3, result={"written": 3})
    local_step, _ = _step("suggested_action", pending=4, local=True, result={"written": 4})
    summary = dr.run_day([gemini_step, local_step], budget=20, print_fn=lambda m: None)
    line = dr.format_log_paragraph(summary, named_entities_remaining=0, model="local-test-model")
    assert "Gemini: 3/20 calls used" in line
    assert "Local: 4 processed" in line
    assert "min elapsed)" in line


def test_ollama_down_in_one_local_step_skips_remaining_local_steps():
    """Phase 5: Ollama being down affects every local step identically --
    don't waste a connection-refused attempt on each remaining one."""
    first, first_calls = _step("suggested_action", pending=1, local=True,
                                result={"written": 0, "ollama_stopped": True})
    second, second_calls = _step("plain_summary", pending=1, local=True)
    summary = dr.run_day([first, second], budget=20, print_fn=lambda m: None)
    assert len(first_calls) == 1     # first was attempted
    assert second_calls == []        # second was skipped, never attempted
    assert "Ollama unavailable" in second.note
    assert summary["quota_stopped"] is False  # this is NOT a quota stop


def test_dry_run_plan_shows_local_step_at_zero_gemini_cost():
    local, _ = _step("suggested_action", pending=9, local=True)
    summary = dr.run_day([local], budget=20, dry_run=True, print_fn=lambda m: None)
    plan = dr.format_dry_run_plan(summary)
    assert "9 pending, LOCAL" in plan
    assert "0 Gemini calls" in plan


# --- quota detection -------------------------------------------------------------------

def test_quota_detected_from_the_gemini_logger_even_without_self_report():
    """ingest_resources does NOT return quota_stopped — it reports 'degraded'.
    Watching the reelbrain.gemini logger catches it anyway."""
    def _run(rows, dry, deadline):
        logging.getLogger("reelbrain.gemini").warning("call failed: 429 RESOURCE_EXHAUSTED")
        return {"written": 0, "degraded": ["x"]}

    silent = dr.Step(name="ingest_resources", find_pending=lambda: [{"i": 1}], run=_run)
    later, later_calls = _step("next", pending=3)
    summary = dr.run_day([silent, later], budget=20, print_fn=lambda m: None)
    assert summary["quota_stopped"] is True
    assert later_calls == []


def test_watcher_ignores_non_quota_errors():
    def _run(rows, dry, deadline):
        logging.getLogger("reelbrain.gemini").warning("call failed: 503 service unavailable")
        return {"written": 1}

    step = dr.Step(name="a", find_pending=lambda: [{"i": 1}], run=_run)
    later, later_calls = _step("b", pending=2)
    summary = dr.run_day([step, later], budget=20, print_fn=lambda m: None)
    assert summary["quota_stopped"] is False
    assert later_calls[0][1] == 2


# --- resilience --------------------------------------------------------------------------

def test_a_failing_pending_lookup_does_not_sink_the_day():
    def _boom():
        raise RuntimeError("notion down")

    broken = dr.Step(name="broken", find_pending=_boom, run=lambda rows, dry, deadline: {})
    healthy, healthy_calls = _step("healthy", pending=2)
    dr.run_day([broken, healthy], budget=20, print_fn=lambda m: None)
    assert healthy_calls[0][1] == 2
    assert "could not list pending work" in broken.note


# --- dry run -------------------------------------------------------------------------------

def test_dry_run_passes_the_flag_through_and_spends_no_budget():
    step, calls = _step("a", pending=5, result={"written": 5})
    summary = dr.run_day([step], budget=20, dry_run=True, print_fn=lambda m: None)
    assert calls[0][2] is True            # dry flag reached the step
    assert summary["used"] == 0           # a preview must never consume budget


# --- the log paragraph -------------------------------------------------------------------

def _summary(steps, used=3, budget=20, quota=False, dry=False):
    return {"budget": budget, "remaining": budget - used, "used": used,
            "quota_stopped": quota, "steps": steps, "dry_run": dry}


def test_log_paragraph_reports_countdown_and_eta():
    step, _ = _step("named_entities", pending=5)
    step.ran, step.pending_before, step.result = True, 5, {"written": 5}
    line = dr.format_log_paragraph(_summary([step]), named_entities_remaining=41)
    assert "named_entities (5/5)" in line
    assert "3/20 calls used" in line
    assert "41 rows left" in line
    assert "~3 more day(s)" in line       # ceil(41/20)


def test_log_paragraph_announces_completion():
    line = dr.format_log_paragraph(_summary([]), named_entities_remaining=0)
    assert "COMPLETE" in line
    assert "unblocked" in line


def test_log_paragraph_reports_quota_stop_and_pending():
    step, _ = _step("plain_summary", pending=9)
    step.pending_before, step.note = 9, "skipped — quota already exhausted this run"
    line = dr.format_log_paragraph(_summary([step], quota=True), named_entities_remaining=10)
    assert "STOPPED on a 429" in line
    assert "plain_summary: 9 pending" in line


def test_log_paragraph_handles_countdown_lookup_failure():
    line = dr.format_log_paragraph(_summary([]), named_entities_remaining=None)
    assert "unavailable" in line


def test_log_paragraph_marks_dry_runs():
    assert "DRY-RUN" in dr.format_log_paragraph(_summary([], dry=True), named_entities_remaining=1)


def test_append_log_writes_a_line(tmp_path):
    path = str(tmp_path / "run.log")
    dr.append_log("first", path)
    dr.append_log("second", path)
    assert open(path, encoding="utf-8").read().splitlines() == ["first", "second"]


# --- the real wiring is at least importable and correctly ordered ------------------------

def test_build_steps_declares_the_documented_priority_order():
    names = [s.name for s in dr.build_steps()]
    assert names == ["named_entities", "recover_placeholders", "enforce_topics",
                     "suggested_action", "plain_summary", "ingest_resources"]


def test_only_enforce_topics_is_free():
    free = [s.name for s in dr.build_steps() if s.free]
    assert free == ["enforce_topics"]


def test_recovery_is_declared_more_expensive_than_a_plain_backfill():
    steps = {s.name: s for s in dr.build_steps()}
    assert steps["recover_placeholders"].cost_per_row > steps["named_entities"].cost_per_row


# --- result-shape normalization (found by the first live dry-run) -------------------

def test_count_handles_both_int_and_list_result_shapes():
    """REGRESSION: the six scripts are NOT uniformly typed. Five report counts
    as ints; ingest_resources reports written/degraded/unreadable as LISTS of
    shortcodes. The first live dry-run crashed on exactly this."""
    assert dr._count(3) == 3
    assert dr._count(["a", "b"]) == 2
    assert dr._count(None) == 0
    assert dr._count("weird") == 0


def test_budget_accounting_works_on_ingest_resources_list_shape():
    step, _ = _step("ingest_resources", pending=10,
                    result={"written": ["a", "b"], "degraded": ["c"],
                            "unreadable": ["d", "e"], "skipped_done": ["f"]})
    summary = dr.run_day([step], budget=20, print_fn=lambda m: None)
    # written(2) + degraded(1) = 3 real Gemini calls.
    # unreadable does NOT count -- the fetch failed before any model call.
    assert summary["used"] == 3


def test_log_paragraph_survives_list_shaped_results():
    step, _ = _step("ingest_resources", pending=3)
    step.ran, step.pending_before = True, 3
    step.result = {"written": ["a", "b"], "unreadable": ["c"]}
    line = dr.format_log_paragraph(_summary([step]), named_entities_remaining=5)
    assert "ingest_resources (2/3)" in line


# --- the dry-run cost plan (row counts + estimated cost per step, zero spend) --------

def test_dry_run_plan_reports_pending_and_estimated_cost_per_step():
    ne, _ = _step("named_entities", pending=5, cost=1)
    rec, _ = _step("recover_placeholders", pending=10, cost=3)
    summary = dr.run_day([ne, rec], budget=20, dry_run=True, print_fn=lambda m: None)
    plan = dr.format_dry_run_plan(summary)
    assert "named_entities: would attempt 5 row(s) @ ~1 call(s) each = ~5 calls" in plan
    # 15 budget left after ne, recover costs 3/row -> 5 rows affordable = ~15 calls
    assert "recover_placeholders: would attempt 5 row(s) @ ~3 call(s) each = ~15 calls" in plan
    assert "~20/20 calls would be spent" in plan


def test_dry_run_plan_shows_free_step_at_zero_cost():
    free, _ = _step("enforce_topics", pending=30, free=True)
    summary = dr.run_day([free], budget=20, dry_run=True, print_fn=lambda m: None)
    plan = dr.format_dry_run_plan(summary)
    assert "enforce_topics: 30 pending, FREE" in plan


def test_dry_run_plan_marks_steps_skipped_when_budget_would_be_gone():
    big, _ = _step("named_entities", pending=100, cost=1)
    later, _ = _step("plain_summary", pending=5, cost=1)
    summary = dr.run_day([big, later], budget=20, dry_run=True, print_fn=lambda m: None)
    plan = dr.format_dry_run_plan(summary)
    assert "plain_summary: 5 pending" in plan and "SKIPPED" in plan


def test_dry_run_plan_spends_no_budget():
    step, _ = _step("named_entities", pending=5, result={"written": 5})
    summary = dr.run_day([step], budget=20, dry_run=True, print_fn=lambda m: None)
    assert summary["used"] == 0


# --- the log names the Gemini model (the quota-incident fix) -------------------------

def test_log_paragraph_names_the_model():
    line = dr.format_log_paragraph(_summary([]), named_entities_remaining=5,
                                   model="gemini-2.5-flash")
    assert "model gemini-2.5-flash" in line


def test_log_paragraph_names_the_model_on_a_429():
    line = dr.format_log_paragraph(_summary([], quota=True), named_entities_remaining=5,
                                   model="gemini-2.5-flash")
    assert "STOPPED on a 429 (model gemini-2.5-flash exhausted)" in line
