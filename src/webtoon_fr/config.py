"""Chargement de config.toml et des réglages utilisateur (LLM)."""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
FICHIER_CONFIG = RACINE / "config.toml"
FICHIER_REGLAGES = RACINE / "reglages.json"

_cache: dict | None = None


def config() -> dict:
    global _cache
    if _cache is None:
        with open(FICHIER_CONFIG, "rb") as f:
            _cache = tomllib.load(f)
    return _cache


def chemin(cle: str) -> Path:
    p = Path(os.path.expanduser(config()["chemins"][cle]))
    return p if p.is_absolute() else RACINE / p


REGLAGES_DEFAUT = {
    "fournisseur": "anthropic",          # anthropic | openai (API compatible OpenAI, ex. LM Studio, Ollama)
    "modele": "claude-sonnet-5-5",
    "cle_api": "",
    "url_base": "",                      # pour un fournisseur compatible OpenAI
    "multimodal": True,
}


def reglages() -> dict:
    r = dict(REGLAGES_DEFAUT)
    if FICHIER_REGLAGES.exists():
        r.update(json.loads(FICHIER_REGLAGES.read_text("utf-8")))
    if not r["cle_api"]:
        r["cle_api"] = os.environ.get("WEBTOON_FR_API_KEY") or (
            os.environ.get("ANTHROPIC_API_KEY", "") if r["fournisseur"] == "anthropic"
            else os.environ.get("OPENAI_API_KEY", "")
        )
    return r


def enregistrer_reglages(nouveaux: dict) -> dict:
    actuels = {}
    if FICHIER_REGLAGES.exists():
        actuels = json.loads(FICHIER_REGLAGES.read_text("utf-8"))
    for k in REGLAGES_DEFAUT:
        if k in nouveaux and nouveaux[k] is not None:
            actuels[k] = nouveaux[k]
    FICHIER_REGLAGES.write_text(json.dumps(actuels, indent=2, ensure_ascii=False), "utf-8")
    try:
        os.chmod(FICHIER_REGLAGES, 0o600)
    except OSError:
        pass
    return reglages()
