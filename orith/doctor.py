"""doctor: report the installation and confirm that no AI or OCR component is used.

ok = false when:
  1. importing every core module (in a fresh interpreter) loads an AI or OCR module, or a module fails to import
  2. a core source file mentions an AI-service host
  3. a country pack is invalid
Reported without failing: AI/OCR packages that happen to be installed in the same Python environment
(the package never imports them), OCR programs on PATH (never called), missing data folders.
"""
from __future__ import annotations

import importlib.metadata as md
import json
import pkgutil
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, paths

PACKAGE_DIR = Path(__file__).resolve().parent

# AI / OCR distributions (normalised names as in importlib.metadata)
AI_DISTRIBUTIONS = {
    "torch", "torchvision", "torchaudio", "tensorflow", "tensorflow-cpu", "tensorflow-intel", "keras", "jax",
    "jaxlib", "flax", "transformers", "tokenizers", "safetensors", "huggingface-hub", "accelerate", "diffusers",
    "sentence-transformers", "timm", "open-clip-torch", "clip", "geoclip", "onnx", "onnxruntime",
    "onnxruntime-gpu", "openvino", "mxnet", "paddlepaddle", "paddleocr", "easyocr", "pytesseract", "tesserocr",
    "ultralytics", "scikit-learn", "xgboost", "lightgbm", "catboost", "spacy", "nltk", "llama-cpp-python",
    "ctransformers", "vllm", "ollama", "openai", "anthropic", "google-generativeai", "google-genai",
    "google-cloud-aiplatform", "vertexai", "cohere", "mistralai", "groq", "together", "replicate",
    "langchain", "langchain-core", "langchain-community", "llama-index", "litellm", "claude-agent-sdk",
}
# module names that must never appear in sys.modules after importing the core
AI_MODULES = {
    "torch", "tensorflow", "keras", "jax", "transformers", "tokenizers", "safetensors", "huggingface_hub",
    "sentence_transformers", "timm", "open_clip", "geoclip", "onnxruntime", "openvino", "paddle", "paddleocr",
    "easyocr", "pytesseract", "tesserocr", "ultralytics", "sklearn", "openai", "anthropic", "google.generativeai",
    "google.genai", "cohere", "mistralai", "ollama", "llama_cpp", "langchain", "llama_index", "litellm", "cv2",
}
# AI-service hosts that core code must not mention (the files defining these lists are skipped)
AI_HOSTS = ["api.openai.com", "api.anthropic.com", "claude.ai", "generativelanguage.googleapis.com",
            "aiplatform.googleapis.com", "api.mistral.ai", "api.cohere", "api.groq.com", "huggingface.co",
            "api.together", "api.replicate.com", "localhost:11434"]
OCR_BINARIES = ["tesseract", "easyocr", "paddleocr"]
LIST_FILES = {"doctor.py", "competition.py"}


def _norm(name):
    return name.lower().replace("_", "-").replace(".", "-")


def installed_distributions():
    out = {}
    for d in md.distributions():
        name = d.metadata.get("Name")
        if name:
            out[_norm(name)] = d.version
    return dict(sorted(out.items()))


def core_modules():
    pkg = PACKAGE_DIR.name
    return sorted(f"{pkg}.{m.name}" for m in pkgutil.iter_modules([str(PACKAGE_DIR)])
                  if not m.name.startswith("_"))


def imported_ai_modules(modules):
    """Import the core modules in a fresh interpreter and return any AI module that got loaded."""
    code = ("import importlib, sys, json\n"
            f"mods = {modules!r}\n"
            "errors = {}\n"
            "for m in mods:\n"
            "    try:\n"
            "        importlib.import_module(m)\n"
            "    except Exception as e:\n"
            "        errors[m] = f'{type(e).__name__}: {e}'\n"
            f"ai = {sorted(AI_MODULES)!r}\n"
            "hit = sorted(n for n in sys.modules if n in ai or n.split('.')[0] in ai)\n"
            "print(json.dumps({'loaded_ai': hit, 'import_errors': errors}))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(PACKAGE_DIR.parent),
                       timeout=120)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"loaded_ai": [], "import_errors": {"<subprocess>": (r.stderr or r.stdout)[-500:]}}


def host_mentions():
    hits = []
    for f in sorted(PACKAGE_DIR.glob("*.py")):
        if f.name in LIST_FILES:
            continue
        text = f.read_text(encoding="utf-8", errors="replace").lower()
        hits += [f"{f.name}: {h}" for h in AI_HOSTS if h in text]
    return hits


def check():
    failures, warnings = [], []
    mods = core_modules()
    imp = imported_ai_modules(mods)
    if imp["loaded_ai"]:
        failures.append({"check": "importing the package loaded AI/OCR modules", "items": imp["loaded_ai"]})
    if imp["import_errors"]:
        failures.append({"check": "package modules failed to import", "items": imp["import_errors"]})
    hosts = host_mentions()
    if hosts:
        failures.append({"check": "package source mentions an AI-service host", "items": hosts})

    dists = installed_distributions()
    ai_dists = sorted(f"{k}=={v}" for k, v in dists.items() if k in AI_DISTRIBUTIONS)
    if ai_dists:
        warnings.append({"note": f"AI/OCR packages are installed in this Python environment; {paths.APP} never "
                                 "imports them (checked above). A dedicated environment (pipx) avoids this.",
                         "items": ai_dists})
    for b in OCR_BINARIES:
        if shutil.which(b):
            warnings.append({"note": f"OCR program on PATH ({paths.APP} never calls it)", "items": [shutil.which(b)]})

    from . import facts, packs, text
    data = {}
    for kind, probe in (("facts", "country_facts.csv"), ("unicode", "Scripts.txt")):
        d = facts.facts_dir() if kind == "facts" else text.unicode_dir()
        data[kind] = {"path": str(d), "present": (d / probe).is_file()}
        if not data[kind]["present"]:
            warnings.append({"note": f"{kind} data missing: the commands that need it will say so", "items": [str(d)]})
    pack_list = []
    try:
        for p in packs.installed():
            info = p.info()
            pack_list.append({k: info[k] for k in ("code", "name", "levels")} |
                             {"credits": [s["credit"] for s in info["sources"]],
                              "licences": sorted({s["licence"] for s in info["sources"]})})
    except packs.PackError as e:
        failures.append({"check": "a country pack is invalid", "items": [str(e)]})

    from . import buildings
    building_tiles = [m["tile"] for m, _ in buildings.tiles()]
    return {"ok": not failures, "version": __version__,
            "buildings": {"dir": str(paths.buildings_dir()), "tiles": building_tiles,
                          "note": "optional GlobalBuildingAtlas layer (CC BY-NC 4.0); install with tools/fetch_gba.py"},
           "python": sys.version.split()[0],
            "platform": platform.platform(), "executable": sys.executable,
            "in_virtualenv": sys.prefix != sys.base_prefix,
            "statement": (f"{paths.APP} is rule-based: no AI model, no OCR engine, no AI service. "
                          "Checked now: no AI/OCR module is loaded by importing every module of the package, and "
                          "no AI-service host appears in its source."),
            "failures": failures, "warnings": warnings, "modules_checked": mods, "data": data,
            "packs": pack_list, "cases_dir": str(paths.cases_dir()), "cache_dir": str(paths.cache_dir()),
            "config_file": str(paths.user_config_path())}
