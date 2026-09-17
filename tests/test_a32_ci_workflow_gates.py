"""CI workflow gates -- A32, closing blocker B3 of the A31-Q1 soft-data survey.

Until A31 both workflows under ``.github/workflows/`` were dead on every branch, for
two independent reasons: each step began with ``cd project-orchestrator`` although that
directory IS the checkout root (so the cd failed and the job never ran), and neither
listed ``fix/**`` in its branch filters, which is the branch every campaign here lives
on. A31 fixed both files. Nothing anchored the fix.

That is the same shape as the generated-doc drift next door in
``tests/test_alias_contract_generated.py``: the file in ``docs/`` rotted through three
waves of tool additions while the suite stayed green, because no test ever opened the
committed file -- the one that existed compared a freshly generated copy in ``tmp_path``
against runtime and so only ever verified the generator. These gates open the committed
YAML.

Every predicate below is ALSO driven with deliberately broken input. A gate that has
only ever been shown the healthy file has not been shown to be capable of failing.

Run:  ./venv312/Scripts/python.exe -m pytest tests/test_a32_ci_workflow_gates.py -v
"""

from pathlib import Path

import pytest
import yaml

WORKFLOW_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"
WORKFLOW_NAMES = ("test-suite-ci.yml", "tool-matrix-ci.yml")
REQUIRED_BRANCH_FILTER = "fix/**"
REPO_ROOT_CD = "cd project-orchestrator"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))


def _on_block(doc: dict) -> dict:
    """The ``on:`` mapping, looked up under BOTH spellings its key can take.

    pyyaml implements YAML 1.1, in which the bare word ``on`` is a BOOLEAN -- so the
    trigger block can arrive under the key ``True`` and ``doc["on"]`` then raises.

    This raises rather than returning ``{}`` on purpose. A helper that swallowed the
    miss would hand every branch-filter assertion below an empty mapping, and an empty
    mapping has no missing events, so the gate would pass vacuously on a workflow whose
    triggers it never actually read. That failure mode is silent, which is the only kind
    worth writing code against.
    """
    for key in ("on", True):
        if key in doc:
            return doc[key]
    raise KeyError(
        "no `on:` trigger block found in workflow; keys=%s" % sorted(map(str, doc)))


def _steps_that_cd_out_of_the_checkout_root(doc: dict) -> list[str]:
    """Labels of every step whose ``run:`` script cds into the repo-root directory name."""
    hits: list[str] = []
    for job_name, job in (doc.get("jobs") or {}).items():
        for i, step in enumerate((job or {}).get("steps") or []):
            if REPO_ROOT_CD in ((step or {}).get("run") or ""):
                label = (step or {}).get("name") or "<unnamed>"
                hits.append(f"{job_name}.steps[{i}] {label!r}")
    return hits


def _events_missing_branch_filter(doc: dict, wanted: str) -> list[str]:
    """Which of push / pull_request fail to list ``wanted`` among their branches."""
    on = _on_block(doc)
    missing: list[str] = []
    for event in ("push", "pull_request"):
        cfg = on.get(event)
        if not isinstance(cfg, dict) or wanted not in (cfg.get("branches") or []):
            missing.append(event)
    return missing


# -- the gates ---------------------------------------------------------------------

@pytest.mark.parametrize("name", WORKFLOW_NAMES)
def test_workflow_file_exists_and_parses(name):
    path = WORKFLOW_DIR / name
    assert path.is_file(), f"missing CI workflow {path}"
    doc = _load(name)
    assert isinstance(doc, dict) and doc.get("jobs"), f"{name} declares no jobs"


@pytest.mark.parametrize("name", WORKFLOW_NAMES)
def test_no_step_cds_into_the_checkout_root(name):
    # The comment at the top of each workflow says "do NOT re-add"; this is what makes
    # that sentence enforceable. Note it reads `run:` only -- the prose warning itself
    # contains the literal string, so a whole-file grep would flag the warning.
    offenders = _steps_that_cd_out_of_the_checkout_root(_load(name))
    assert offenders == [], (
        f"{name}: {REPO_ROOT_CD!r} is back in a run: block ({offenders}). That path is "
        "the checkout root, so the cd fails and the whole gate silently never executes.")


@pytest.mark.parametrize("name", WORKFLOW_NAMES)
def test_push_and_pull_request_both_trigger_on_the_campaign_branch_pattern(name):
    missing = _events_missing_branch_filter(_load(name), REQUIRED_BRANCH_FILTER)
    assert missing == [], (
        f"{name}: events {missing} do not list {REQUIRED_BRANCH_FILTER!r}; campaign work "
        "happens on fix/** branches, so the gate would not run where it is needed.")


# -- positive controls -------------------------------------------------------------

def test_control_a_cd_step_is_reported():
    broken = yaml.safe_load(
        "jobs:\n"
        "  j:\n"
        "    steps:\n"
        "      - name: Checkout\n"
        "        uses: actions/checkout@v4\n"
        "      - name: Run pytest\n"
        "        run: |\n"
        "          cd project-orchestrator\n"
        "          python -m pytest tests -q\n")
    assert _steps_that_cd_out_of_the_checkout_root(broken) == [
        "j.steps[1] 'Run pytest'"]


def test_control_a_step_without_a_run_block_is_not_reported():
    # Negative half of the control: `uses:`-only steps have no run script at all, and
    # must not be mistaken for offenders by a None-vs-str slip.
    fine = yaml.safe_load(
        "jobs:\n  j:\n    steps:\n      - uses: actions/checkout@v4\n")
    assert _steps_that_cd_out_of_the_checkout_root(fine) == []


def test_control_a_missing_branch_filter_is_reported():
    broken = yaml.safe_load(
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "  pull_request:\n"
        "    branches: [main, \"fix/**\"]\n")
    assert _events_missing_branch_filter(broken, "fix/**") == ["push"]


def test_control_an_event_declared_without_branches_is_reported():
    broken = yaml.safe_load("on:\n  push:\n  pull_request:\n    branches: [\"fix/**\"]\n")
    assert _events_missing_branch_filter(broken, "fix/**") == ["push"]


def test_control_the_on_key_is_the_yaml_1_1_boolean_and_is_still_found():
    # Measured, not assumed: this pins which key pyyaml actually produces for `on:`.
    # If a future pyyaml resolves it as the string "on", this test fails and says so --
    # which is the point. _on_block handles either, so the gates above stay correct.
    doc = yaml.safe_load("on:\n  push:\n    branches: [\"fix/**\"]\n")
    assert list(doc) == [True], (
        f"pyyaml no longer folds `on:` to the boolean True; observed key {list(doc)!r}")
    assert _on_block(doc) == {"push": {"branches": ["fix/**"]}}


def test_control_a_workflow_with_no_on_block_raises_instead_of_passing_vacuously():
    with pytest.raises(KeyError):
        _events_missing_branch_filter({"jobs": {}}, "fix/**")
