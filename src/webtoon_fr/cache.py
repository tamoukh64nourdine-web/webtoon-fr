"""Cache clé/valeur sur disque (JSON), clé = hash du contenu."""
from __future__ import annotations

import json
from pathlib import Path


class Cache:
    def __init__(self, dossier: Path):
        self.dossier = dossier
        self.dossier.mkdir(parents=True, exist_ok=True)

    def _f(self, cle: str) -> Path:
        return self.dossier / f"{cle[:80]}.json"

    def lire(self, cle: str):
        f = self._f(cle)
        if f.exists():
            try:
                return json.loads(f.read_text("utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    def ecrire(self, cle: str, valeur) -> None:
        self._f(cle).write_text(json.dumps(valeur, ensure_ascii=False), "utf-8")
