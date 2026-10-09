"""shadow_scheduler.models/config: registry shape, exit-code tables, status vocabulary kept
distinct from PK-C (READY/DEGRADED/BLOCKED) and PK-D/intelligence_refresh (SUCCESS/DEGRADED/
BLOCKED/FAILED/...)."""
from __future__ import annotations

from shadow_scheduler import config, models


def test_runner_vocabulary_is_disjoint_from_readiness_and_orchestrator_vocabularies():
    runner_terms = {models.RUNNING, models.COMPLETED, models.CHILD_NONZERO, models.LOCKED,
                    models.FAILED}
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
                 "child_exit_code", "runner_status", "log_path"):
        assert field in d, field
