"""Ligne de commande : webtoon-fr web | run | fetch | analyze | render."""
from __future__ import annotations

import argparse
from pathlib import Path

from . import pipeline
from .fetch import recuperer
from .models import dossier_chapitre


def _progres(etape, message, fraction=None):
    if fraction is None or fraction >= 1.0:
        print(f"[{etape}] {message}", flush=True)


def utiliser_certificats_systeme():
    """Python utilise les certificats du système (Windows, macOS) au lieu de sa propre liste.

    Nécessaire quand un antivirus ou un proxy d'entreprise inspecte les connexions HTTPS :
    la vérification reste active, elle s'appuie simplement sur les certificats auxquels le système fait confiance.
    """
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


def main(argv=None):
    utiliser_certificats_systeme()
    p = argparse.ArgumentParser(prog="webtoon-fr", description="Traduction de webtoons anglais -> français")
    sp = p.add_subparsers(dest="cmd", required=True)

    w = sp.add_parser("web", help="lancer l'interface web")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--hote", default="127.0.0.1")

    for nom, aide in (("run", "tout faire"), ("analyze", "phase 1 : analyse + traduction"), ("fetch", "récupérer les images")):
        s = sp.add_parser(nom, help=aide)
        s.add_argument("source", help="lien de la page du chapitre, ou dossier d'images")
        s.add_argument("--serie", required=True)
        s.add_argument("--chapitre", required=True)

    r = sp.add_parser("render", help="phase 2 : nettoyage, rendu, export")
    r.add_argument("--serie", required=True)
    r.add_argument("--chapitre", required=True)

    a = p.parse_args(argv)
    if a.cmd == "web":
        import uvicorn
        print(f"Interface : http://{a.hote}:{a.port}")
        uvicorn.run("webtoon_fr.web.app:app", host=a.hote, port=a.port, log_level="warning")
        return
    if a.cmd == "render":
        pipeline.rendre_chapitre(a.serie, a.chapitre, _progres)
        return
    url, fichiers = None, None
    if a.source.startswith(("http://", "https://")):
        url = a.source
    else:
        d = Path(a.source)
        fichiers = sorted(f for f in d.iterdir() if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    if a.cmd == "fetch":
        if not url:
            p.error("fetch attend un lien")
        recuperer(url, dossier_chapitre(a.serie, a.chapitre) / "source", lambda m, f=None: print(m))
    elif a.cmd == "analyze":
        pipeline.analyser(a.serie, a.chapitre, url, fichiers, _progres)
    else:
        pipeline.tout(a.serie, a.chapitre, url, fichiers, _progres)


if __name__ == "__main__":
    main()
