"""The package must import and run with no AI component; `doctor` reports and checks that."""
from pathlib import Path

from orith import doctor


def test_importing_every_module_loads_no_ai_module():
    mods = doctor.core_modules()
    assert any(m.endswith(".cli") for m in mods) and any(m.endswith(".ledger") for m in mods)
    res = doctor.imported_ai_modules(mods)
    assert res["import_errors"] == {}
    assert res["loaded_ai"] == []


def test_source_never_imports_dev_code_or_ai_libraries():
    banned = ("import torch", "from torch", "geoclip", "import transformers", "from transformers", "from dev",
              "import dev", "tn_compat", "pytesseract", "import openai", "import anthropic")
    for f in doctor.PACKAGE_DIR.glob("*.py"):
        if f.name in doctor.LIST_FILES:
            continue
        text = f.read_text(encoding="utf-8")
        assert not [b for b in banned if b in text], f.name


def test_source_has_no_country_specific_logic():
    for f in doctor.PACKAGE_DIR.glob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        for word in ("tunisia", "governorate", "delegation", "map-tunisia"):
            assert word not in text, f"{f.name} mentions '{word}'"


def test_doctor_reports_and_only_warns_about_unused_ai_packages():
    r = doctor.check()
    assert r["ok"] is True and "no AI" in r["statement"] and r["python"]
    assert {"facts", "unicode"} <= set(r["data"]) and Path(r["cases_dir"]).name == "cases"
    for w in r["warnings"]:  # AI packages present in the environment are reported, never a failure
        assert "note" in w
    assert "torch" in doctor.AI_DISTRIBUTIONS and "api.openai.com" in doctor.AI_HOSTS
