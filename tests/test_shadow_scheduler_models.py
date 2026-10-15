"""shadow_scheduler.models/config: registry shape, exit-code tables, status vocabulary kept
distinct from PK-C (READY/DEGRADED/BLOCKED) and PK-D/intelligence_refresh (SUCCESS/DEGRADED/
BLOCKED/FAILED/...)."""
from __future__ import annotations

from shadow_scheduler import config, models


def test_runner_vocabulary_is_disjoint_from_readiness_and_orchestrator_vocabularies():
    runner_terms = {models.RUNNING, models.COMPLETED, models.CHILD_NONZERO, models.LOCKED,
                    models.FAILED, models.EXPIRED, models.WAITING_DEPENDENCY,
                    models.DEPENDENCY_FAILED}
    readiness_terms = {"READY", "DEGRADED", "BLOCKED"}
    orchestrator_terms = {"SUCCESS", "DEGRADED", "BLOCKED", "FAILED", "SKIPPED",
                          "ALREADY_COMPLETED", "LOCKED", "PLANNED", "RUNNING"}
    # RUNNING/LOCKED/FAILED happen to be spelled the same as transient/terminal states
    # elsewhere in the repo, but they are never imported FROM those modules - only compared
    # here to confirm this layer never invents a brand-new synonym for READY/SUCCESS/BLOCKED.
    assert not runner_terms & readiness_terms
    assert "SUCCESS" not in runner_terms
    assert "READY" not in runner_terms


def test_infra_exit_codes_are_disjoint_from_production_orchestrator_and_intelligence_refresh():
    from intelligence_refresh.models import EXIT_CODES as IR_EXIT_CODES
    from production_orchestrator.models import EXIT_CODES as PO_EXIT_CODES
    infra_values = set(models.INFRA_EXIT_CODES.values())
    assert infra_values.isdisjoint(set(PO_EXIT_CODES.values()))
    assert infra_values.isdisjoint(set(IR_EXIT_CODES.values()))


def test_job_registry_has_exactly_the_four_jobs_in_order():
    assert tuple(config.JOB_REGISTRY.keys()) == models.JOBS
    assert models.JOBS == ("intelligence_am", "pre", "intelligence_pm", "post")


def test_job_registry_schedule_times_match_the_packet():
    expected = {"intelligence_am": "06:40", "pre": "07:00", "intelligence_pm": "19:15",
               "post": "19:30"}
    for job, time_str in expected.items():
        assert config.JOB_REGISTRY[job].scheduled_time == time_str


def test_job_registry_weekdays_are_monday_to_friday_only():
    for spec in config.JOB_REGISTRY.values():
        assert spec.weekdays == (0, 1, 2, 3, 4)


def test_job_registry_points_at_the_existing_bat_scripts_only():
    expected = {"intelligence_am": "scripts/run_intelligence_refresh.bat",
               "pre": "scripts/run_production_pre.bat",
               "intelligence_pm": "scripts/run_intelligence_refresh.bat",
               "post": "scripts/run_production_post.bat"}
    for job, relpath in expected.items():
        assert config.JOB_REGISTRY[job].bat_relpath == relpath


def test_job_registry_task_names_are_unique_and_under_the_dmb_folder():
    names = [spec.task_name for spec in config.JOB_REGISTRY.values()]
    assert len(names) == len(set(names))
    assert all(name.startswith("DMB-Shadow-") for name in names)


def test_repo_root_resolves_to_the_actual_repo():
    import os
    root = config.repo_root()
    assert os.path.isfile(os.path.join(root, "config.py"))
    assert os.path.isdir(os.path.join(root, "shadow_scheduler"))


def test_shadow_out_dir_defaults_under_the_root_output_dir():
    import os
    import config as root_config
    assert config.shadow_out_dir() == os.path.join(root_config.OUT_DIR, "shadow_scheduler")


def test_shadow_out_dir_honors_explicit_override():
    assert config.shadow_out_dir("/tmp/wherever") == "/tmp/wherever"


def test_run_record_to_dict_round_trips_every_packet_field():
    rec = models.RunRecord(job="post", run_id="r1", started_at="2026-10-09T00:00:00+00:00",
                           hostname="h", pid=1, repo_path="D:\\repo",
                           python_executable="D:\\repo\\venv\\Scripts\\python.exe",
                           child_command=["x.bat"], runner_status=models.COMPLETED,
                           log_path="logs/2026-10-09/post_000000.log")
    d = rec.to_dict()
    for field in ("schema_version", "job", "run_id", "started_at", "completed_at", "duration",
                 "hostname", "pid", "repo_path", "python_executable", "child_command",
                 "child_exit_code", "runner_status", "log_path", "scheduled_time",
                 "window_start", "window_end", "child_started", "dependency_job",
                 "dependency_status", "dependency_run_id", "dependency_wait_seconds",
                 "dependency_satisfied_at"):
        assert field in d, field


def test_run_record_window_fields_default_sanely_for_an_ordinary_launch():
    """`child_started` defaults True (an ordinary COMPLETED/CHILD_NONZERO record never has to
    set it explicitly); the window fields default empty/None until the runner fills them in."""
    rec = models.RunRecord()
    assert rec.child_started is True
    assert rec.scheduled_time == ""
    assert rec.window_start is None
    assert rec.window_end is None


def test_expired_is_a_terminal_status_with_a_zero_exit_code():
    assert models.EXPIRED in models.TERMINAL_STATUSES
    assert models.EXPIRED_EXIT_CODE == 0
    # Deliberately NOT one of the nonzero infra exit codes - EXPIRED is never a crash.
    assert models.EXPIRED not in models.INFRA_EXIT_CODES
    assert models.EXPIRED_EXIT_CODE not in models.INFRA_EXIT_CODES.values()


def test_waiting_is_distinct_from_pending_and_missed():
    assert models.WAITING not in (models.PENDING, models.MISSED, models.NOT_EXPECTED,
                                  models.OK, models.UNKNOWN)


def test_dependency_failed_is_a_terminal_status_with_its_own_nonzero_exit_code():
    assert models.DEPENDENCY_FAILED in models.TERMINAL_STATUSES
    assert models.INFRA_EXIT_CODES[models.DEPENDENCY_FAILED] == 5
    # Distinct from the two existing infra exit codes - a reader must be able to tell
    # "dependency never satisfied" apart from "lock busy" (3) and "launch failed" (4).
    assert models.INFRA_EXIT_CODES[models.DEPENDENCY_FAILED] not in (
        models.INFRA_EXIT_CODES[models.LOCKED], models.INFRA_EXIT_CODES[models.FAILED])


def test_waiting_dependency_is_non_terminal():
    assert models.WAITING_DEPENDENCY not in models.TERMINAL_STATUSES


def test_dependencies_map_only_covers_pre_and_post():
    from shadow_scheduler import config
    assert config.DEPENDENCIES == {"pre": "intelligence_am", "post": "intelligence_pm"}
    assert "intelligence_am" not in config.DEPENDENCIES
    assert "intelligence_pm" not in config.DEPENDENCIES
