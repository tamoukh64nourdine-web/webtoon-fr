"""Orchestration : phase 1 (analyse + traduction) et phase 2 (nettoyage + rendu + export).

Le fichier chapitre.json sépare les deux phases : on peut le corriger, puis relancer seulement le rendu.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .cache import Cache
from .clean import nettoyer
from .config import config
from .detect import detecter
from .export import exporter
from .fetch import importer_page_enregistree, recuperer
from .group import construire_bulles, reporter_verrouillees
from .ingest import lister_images, recoller
from .models import Chapitre, ajouter_resume, dossier_chapitre, ecrire_glossaire, lire_glossaire
from .render import rendre, verifier_glyphes, verifier_licence
from .translate import ErreurTraduction, traduire


def _noop(*a):
    pass


def preparer_source(serie: str, numero: str, url: str | list[str] | None = None, fichiers: list[Path] | None = None,
                    progres=_noop, page_enregistree: list[Path] | None = None) -> Path:
    """Remplit source/ : depuis un ou plusieurs liens, une page enregistrée, ou des images importées.

    Sans aucune de ces sources, utilise ce qui est déjà dans source/ (récupération vérifiée à l'avance).
    """
    dossier = dossier_chapitre(serie, numero)
    source = dossier / "source"
    if url:
        recuperer(url, source, progres)
    elif page_enregistree:
        importer_page_enregistree(page_enregistree, source, progres)
    elif fichiers:
        source.mkdir(parents=True, exist_ok=True)
        for f in source.iterdir():
            if f.is_file():
                f.unlink()
        for f in fichiers:
            shutil.copy2(f, source / f.name)
    if not source.exists() or not lister_images(source):
        raise ValueError("Aucune image source pour ce chapitre.")
    return dossier


def analyser(serie: str, numero: str, url: str | list[str] | None = None, fichiers: list[Path] | None = None,
             progres=_noop, traduire_aussi: bool = True, page_enregistree: list[Path] | None = None) -> Chapitre:
    dossier = preparer_source(serie, numero, url, fichiers, lambda m, f=None: progres("recuperation", m, f),
                              page_enregistree)
    cache = Cache(dossier / "cache")
    progres("recollage", "Recollage des images en une bande…", None)
    bande, info = recoller(lister_images(dossier / "source"))
    if url:
        info["url"] = url

    ancien = None
    if (dossier / "chapitre.json").exists():
        ancien = Chapitre.charger(serie, numero)
    chap = Chapitre(serie=serie, numero=str(numero), source=info)
    if ancien:
        chap.resume = ancien.resume

    regions = detecter(bande, lambda m, f=None: progres("detection", m, f), cache)
    g = lire_glossaire(serie)
    noms = list(g.get("noms", {}).keys())
    bulles = construire_bulles(bande, regions, dossier / "masques", noms,
                               lambda m, f=None: progres("ocr", m, f), cache)
    chap.bulles = reporter_verrouillees(bulles, ancien.bulles if ancien else [])
    chap.etat = "analyse"
    chap.sauver(historiser=True)
    progres("ocr", f"{len(chap.bulles)} bulles détectées.", 1.0)

    if traduire_aussi:
        traduire_chapitre(chap, bande, progres, cache)
    return chap


def traduire_chapitre(chap: Chapitre, bande=None, progres=_noop, cache=None) -> Chapitre:
    dossier = chap.dossier
    if bande is None:
        bande, _ = recoller(lister_images(dossier / "source"))
    cache = cache or Cache(dossier / "cache")
    try:
        props = traduire(chap, bande, lambda m, f=None: progres("traduction", m, f), lambda: chap.sauver(), cache)
    except ErreurTraduction:
        chap.sauver()
        raise
    g = lire_glossaire(chap.serie)
    p = g.setdefault("propositions", {"noms": [], "termes": {}, "registres": []})
    connus = set(g.get("noms", {})) | set(p.get("noms", []))
    p["noms"] = p.get("noms", []) + [n for n in dict.fromkeys(props.get("noms", [])) if n not in connus]
    for k, v in props.get("termes", {}).items():
        if k not in g.get("termes", {}):
            p.setdefault("termes", {})[k] = v
    deja = {(r.get("a"), r.get("b")) for r in g.get("registres", []) + p.get("registres", [])}
    for r in props.get("registres", []):
        if (r.get("a"), r.get("b")) not in deja and (r.get("b"), r.get("a")) not in deja:
            p.setdefault("registres", []).append(r)
            deja.add((r.get("a"), r.get("b")))
    ecrire_glossaire(chap.serie, g)
    ajouter_resume(chap.serie, chap.numero, chap.resume)
    chap.etat = "traduit"
    chap.sauver()
    progres("traduction", "Traduction terminée.", 1.0)
    return chap


def rendre_chapitre(serie: str, numero: str, progres=_noop) -> Chapitre:
    chap = Chapitre.charger(serie, numero)
    cfg = config()["rendu"]
    verifier_licence(cfg["police"])
    manquants = verifier_glyphes(cfg["police"])
    if manquants:
        progres("rendu", "Attention, caractères absents de la police : " + " ".join(manquants), None)
    progres("rendu", "Recollage de la bande source…", None)
    source, _ = recoller(lister_images(chap.dossier / "source"))
    propre = nettoyer(source, chap.bulles, chap.dossier, lambda m, f=None: progres("nettoyage", m, f))
    finale = rendre(propre, chap.bulles, chap.dossier, lambda m, f=None: progres("rendu", m, f))
    progres("export", "Export des images…", None)
    noms = exporter(finale, source, chap.dossier, chap.source.get("format", "jpg"),
                    f"{chap.serie} — chapitre {chap.numero}")
    chap.source["sortie"] = noms
    chap.etat = "rendu"
    chap.sauver()
    progres("export", f"{len(noms)} images exportées.", 1.0)
    return chap


def tout(serie: str, numero: str, url: str | list[str] | None = None, fichiers: list[Path] | None = None,
         progres=_noop, page_enregistree: list[Path] | None = None) -> Chapitre:
    analyser(serie, numero, url, fichiers, progres, page_enregistree=page_enregistree)
    return rendre_chapitre(serie, numero, progres)
