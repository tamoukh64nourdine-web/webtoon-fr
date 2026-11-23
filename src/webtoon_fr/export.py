"""8. Export : redécoupe de la bande en images, coupées sur des lignes vides, et page HTML de lecture."""
from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from .config import config
from .ingest import segments


def exporter(bande: np.ndarray, source: np.ndarray, dossier: Path, fmt: str, titre: str) -> list[str]:
    cfg = config()["export"]
    sortie = dossier / "sortie"
    apercu = dossier / "apercu"
    for d in (sortie, apercu):
        d.mkdir(parents=True, exist_ok=True)
        for f in d.iterdir():
            if f.is_file():
                f.unlink()
    noms = []
    for i, (y0, y1, _, _) in enumerate(segments(bande, cfg["hauteur_image"]), start=1):
        im = Image.fromarray(bande[y0:y1])
        if fmt == "png":
            nom = f"{i:03d}.png"
            im.save(sortie / nom, optimize=True)
        else:
            nom = f"{i:03d}.jpg"
            # qualité 95 sans sous-échantillonnage des couleurs (4:4:4)
            im.save(sortie / nom, "JPEG", quality=cfg["qualite_jpg"], subsampling=0)
        Image.fromarray(source[y0:y1]).save(apercu / f"{i:03d}.jpg", "JPEG", quality=85)
        noms.append(nom)
    (sortie / "index.html").write_text(_lecteur(titre, noms), "utf-8")
    return noms


def _lecteur(titre: str, noms: list[str]) -> str:
    imgs = "\n".join(f'<img src="{n}" alt="">' for n in noms)
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titre}</title>
<style>body{{margin:0;background:#111}}main{{max-width:800px;margin:0 auto}}img{{display:block;width:100%;height:auto}}
h1{{color:#eee;font:600 16px system-ui;padding:12px;margin:0}}</style></head>
<body><main><h1>{titre}</h1>
{imgs}
</main></body></html>
"""


def archive(dossier: Path) -> Path:
    z = dossier / f"{dossier.name}-fr.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted((dossier / "sortie").iterdir()):
            zf.write(f, f.name)
    return z
