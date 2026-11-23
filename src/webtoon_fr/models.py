"""Structures de données : Bulle, Chapitre, Série, et lecture/écriture du JSON."""
from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import chemin, config


@dataclass
class Bulle:
    id: int
    type: str                      # bulle | narration | texte_libre
    boite: list[int]               # [x0, y0, x1, y1] dans le repère de la bande
    masque: str = ""               # chemin relatif du masque PNG (découpé à la boîte)
    lignes: list[list[int]] = field(default_factory=list)  # boîtes des lignes de texte
    anglais: str = ""
    anglais_corrige: str | None = None
    majuscules: bool = False
    emphase: bool = False
    confiance_ocr: float = 1.0
    groupe: int | None = None
    taille_police: int | None = None
    francais: str = ""
    locuteur: str | None = None
    destinataire: str | None = None
    registre: str | None = None
    ordre_suspect: bool = False
    a_verifier: bool = False
    verrouille: bool = False
    style: dict = field(default_factory=lambda: {"couleur": "#1a1a1a", "contour": None, "fond": "#ffffff"})
    notes: list[str] = field(default_factory=list)

    @property
    def texte_source(self) -> str:
        return self.anglais_corrige or self.anglais


@dataclass
class Chapitre:
    serie: str
    numero: str
    source: dict = field(default_factory=dict)   # largeur, hauteur, decoupes, format, url
    bulles: list[Bulle] = field(default_factory=list)
    resume: str = ""
    etat: str = "nouveau"                        # nouveau | analyse | traduit | rendu | erreur
    maj: float = 0.0

    # --- chemins ---------------------------------------------------------
    @property
    def dossier(self) -> Path:
        return dossier_chapitre(self.serie, self.numero)

    @property
    def fichier(self) -> Path:
        return self.dossier / "chapitre.json"

    # --- E/S -------------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["chapitre"] = f"{self.serie}/ch-{self.numero}"
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Chapitre":
        bulles = [Bulle(**{k: v for k, v in b.items() if k in Bulle.__dataclass_fields__}) for b in d.get("bulles", [])]
        return cls(serie=d["serie"], numero=str(d["numero"]), source=d.get("source", {}), bulles=bulles,
                   resume=d.get("resume", ""), etat=d.get("etat", "nouveau"), maj=d.get("maj", 0.0))

    def sauver(self, historiser: bool = False) -> None:
        self.dossier.mkdir(parents=True, exist_ok=True)
        if historiser and self.fichier.exists():
            hist = self.dossier / "historique"
            hist.mkdir(exist_ok=True)
            shutil.copy2(self.fichier, hist / f"chapitre-{time.strftime('%Y%m%d-%H%M%S')}.json")
            anciens = sorted(hist.glob("chapitre-*.json"))
            for f in anciens[: max(0, len(anciens) - config()["export"]["historique_max"])]:
                f.unlink()
        self.maj = time.time()
        tmp = self.fichier.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), "utf-8")
        tmp.replace(self.fichier)

    @classmethod
    def charger(cls, serie: str, numero: str) -> "Chapitre":
        f = dossier_chapitre(serie, numero) / "chapitre.json"
        return cls.from_dict(json.loads(f.read_text("utf-8")))

    def bulle(self, id_: int) -> Bulle:
        for b in self.bulles:
            if b.id == id_:
                return b
        raise KeyError(id_)


# --- séries -------------------------------------------------------------

def nom_valide(nom: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", nom.lower()).strip("-")
    if not s:
        raise ValueError("nom de série vide")
    return s


def dossier_serie(serie: str) -> Path:
    return chemin("series") / nom_valide(serie)


def dossier_chapitre(serie: str, numero: str) -> Path:
    num = re.sub(r"[^0-9a-zA-Z.-]", "", str(numero)) or "0"
    return dossier_serie(serie) / f"ch-{num}"


GLOSSAIRE_DEFAUT = {
    "noms": {},            # "Jin": "Jin"
    "termes": {},          # "Hunter": "Chasseur"
    "honorifiques": "garder",  # garder | adapter
    "registres": [],       # [{"a": "Jin", "b": "Seo-yeon", "registre": "tu"}]
    "propositions": {"noms": [], "termes": {}, "registres": []},
}


def lire_glossaire(serie: str) -> dict:
    f = dossier_serie(serie) / "glossaire.json"
    g = json.loads(json.dumps(GLOSSAIRE_DEFAUT))
    if f.exists():
        g.update(json.loads(f.read_text("utf-8")))
    return g


def ecrire_glossaire(serie: str, g: dict) -> None:
    d = dossier_serie(serie)
    d.mkdir(parents=True, exist_ok=True)
    (d / "glossaire.json").write_text(json.dumps(g, indent=2, ensure_ascii=False), "utf-8")


def lire_contexte(serie: str) -> str:
    f = dossier_serie(serie) / "contexte.md"
    return f.read_text("utf-8") if f.exists() else ""


def ecrire_contexte(serie: str, texte: str) -> None:
    d = dossier_serie(serie)
    d.mkdir(parents=True, exist_ok=True)
    (d / "contexte.md").write_text(texte, "utf-8")


def resumes_precedents(serie: str, numero: str, n: int = 3) -> list[tuple[str, str]]:
    """Résumés des n chapitres précédant `numero` selon leur numéro (pas l'ordre de traitement)."""
    texte = lire_contexte(serie)
    blocs = re.split(r"^##\s*Chapitre\s+", texte, flags=re.M)
    res = []
    for b in blocs[1:]:
        tete, _, corps = b.partition("\n")
        num = tete.strip()
        res.append((num, corps.strip()))
    try:
        cible = float(numero)
        res = [r for r in res if _num(r[0]) is not None and _num(r[0]) < cible]
        res.sort(key=lambda r: _num(r[0]))
    except ValueError:
        pass
    return res[-n:]


def ajouter_resume(serie: str, numero: str, resume: str) -> None:
    if not resume.strip():
        return
    texte = lire_contexte(serie)
    if re.search(rf"^##\s*Chapitre\s+{re.escape(str(numero))}\s*$", texte, flags=re.M):
        return  # déjà présent : on ne remplace jamais un résumé existant
    if not texte.strip():
        texte = "# Contexte de la série\n\n## Synopsis\n\n(À compléter.)\n\n## Personnages\n\n(À compléter.)\n"
    texte = texte.rstrip() + f"\n\n## Chapitre {numero}\n\n{resume.strip()}\n"
    ecrire_contexte(serie, texte)


def _num(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def lister_series() -> list[dict]:
    base = chemin("series")
    base.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        chs = []
        for c in d.glob("ch-*"):
            f = c / "chapitre.json"
            rapport = c / "source" / "rapport.json"
            if not f.exists() and rapport.exists():
                r = json.loads(rapport.read_text("utf-8"))
                res = r.get("resume", {})
                chs.append({"numero": c.name[3:], "etat": "recupere_incomplet" if res.get("manquantes") else "recupere",
                            "bulles": 0, "a_verifier": 0, "images": res.get("gardees", 0), "maj": rapport.stat().st_mtime})
                continue
            if f.exists():
                j = json.loads(f.read_text("utf-8"))
                n_verif = sum(1 for b in j.get("bulles", []) if b.get("a_verifier"))
                chs.append({"numero": j["numero"], "etat": j.get("etat"), "bulles": len(j.get("bulles", [])),
                            "a_verifier": n_verif, "maj": j.get("maj", 0)})
        chs.sort(key=lambda c: (_num(c["numero"]) is None, _num(c["numero"]) or 0, c["numero"]))
        out.append({"serie": d.name, "chapitres": chs})
    return out
