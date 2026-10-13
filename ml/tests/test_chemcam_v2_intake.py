import importlib.util
import sys
from pathlib import Path


def load_intake():
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    previous = sys.path[:]
    try:
        sys.path.insert(0, str(scripts))
        spec = importlib.util.spec_from_file_location(
            "chemcam_v2_intake", scripts / "prepare_chemcam_v2_intake.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = previous


intake = load_intake()


def row(target, spectrum="", sample=""):
    return {"Target": target, "Spectrum Name": spectrum, "Sample Name": sample}


def test_seen_guard_follows_transitive_published_aliases_across_previous_test():
    rows = [row("New-name", "old_name"), row("new name", "", "Bridge")]
    rows += [row("final-candidate", "bridge"), row("truly-new")]
    v1 = {"development": [], "test": [{"composition_row": row("OLD-NAME")}]}
    seen = intake.seen_alias_closure(rows, v1, {"test_sample_ids": []})
    assert {"oldname", "newname", "bridge", "finalcandidate"} <= seen
    assert "trulynew" not in seen


def test_seen_guard_includes_old_preflight_but_does_not_invent_prefix_aliases():
    rows = [row("JR-1"), row("JR-3"), row("Li420_NCS_JR3")]
    v1 = {"development": [], "test": []}
    seen = intake.seen_alias_closure(rows, v1, {"test_sample_ids": ["JR1"]})
    assert seen == {"jr1"}
    # Family exclusions are separately explicit: Li420 is never an extra base material.
    assert all(folder != "li420ncsjr3" for _, folder, _ in intake.SELECTION)
