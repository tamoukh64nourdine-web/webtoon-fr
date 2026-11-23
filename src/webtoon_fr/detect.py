"""2. Tuilage et détection : lignes de texte (OCR) puis bulles (masques).

Moteurs :
- "ocr"    : détection des lignes par l'OCR, puis forme de la bulle par croissance de région
             autour du texte (fonctionne sans aucun modèle à télécharger).
- "rtdetr" : en plus, détecteur de bulles RT-DETR-v2 (ogkalu/comic-text-and-bubble-detector),
             si `transformers` et `torch` sont installés. Ses bulles servent à regrouper les lignes.
- "auto"   : rtdetr si disponible, sinon ocr.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import config
from .ingest import segments
from .ocr import moteur_ocr, lire


@dataclass
class Ligne:
    boite: list[int]          # x0, y0, x1, y1 (repère de la bande)
    texte: str
    score: float

    @property
    def h(self) -> int:
        return self.boite[3] - self.boite[1]

    @property
    def centre(self) -> tuple[int, int]:
        return ((self.boite[0] + self.boite[2]) // 2, (self.boite[1] + self.boite[3]) // 2)


@dataclass
class Region:
    type: str                         # bulle | narration | texte_libre
    x0: int
    y0: int
    masque: np.ndarray                # bool, découpé à la boîte
    lignes: list[Ligne] = field(default_factory=list)
    fond: tuple[int, int, int] = (255, 255, 255)
    groupe: int | None = None

    @property
    def boite(self) -> list[int]:
        h, w = self.masque.shape
        return [self.x0, self.y0, self.x0 + w, self.y0 + h]

    def contient(self, x: int, y: int) -> bool:
        h, w = self.masque.shape
        xi, yi = x - self.x0, y - self.y0
        return 0 <= xi < w and 0 <= yi < h and bool(self.masque[yi, xi])


# --- utilitaires -----------------------------------------------------------

def iou(a, b) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    if inter == 0:
        return 0.0
    aa = (a[2] - a[0]) * (a[3] - a[1])
    ab = (b[2] - b[0]) * (b[3] - b[1])
    return inter / float(aa + ab - inter)


def recouvrement_horizontal(a, b) -> float:
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    return inter / max(1, min(a[2] - a[0], b[2] - b[0]))


def _a_du_texte(t: str) -> bool:
    return sum(c.isalnum() for c in t) >= 1


# --- étape A : lignes de texte par tuiles -------------------------------------

def detecter_lignes(bande: np.ndarray, progres=lambda *a: None, cache=None) -> list[Ligne]:
    cfg = config()["detection"]
    ov = cfg["chevauchement"]
    H = bande.shape[0]
    segs = segments(bande, cfg["hauteur_tuile"])
    lignes: list[Ligne] = []
    for i, (y0, y1, haut_propre, bas_propre) in enumerate(segs):
        t0 = y0 if haut_propre else max(0, y0 - ov)
        t1 = y1 if bas_propre else min(H, y1 + ov)
        tuile = bande[t0:t1]
        progres(f"Détection du texte : tuile {i + 1}/{len(segs)}", (i + 1) / len(segs))
        res = lire(tuile, cache=cache)
        for boite, texte, score in res:
            x0, yy0, x1, yy1 = boite
            # une ligne qui touche un bord de recouvrement est vue entière dans la tuile voisine
            if (t0 > 0 and not haut_propre and yy0 <= 3) or (t1 < H and not bas_propre and yy1 >= tuile.shape[0] - 3):
                continue
            if score < cfg["seuil_score_ligne"] or not _a_du_texte(texte):
                continue
            lignes.append(Ligne([int(x0), int(yy0 + t0), int(x1), int(yy1 + t0)], texte.strip(), float(score)))
    # doublons des zones de recouvrement
    lignes.sort(key=lambda l: -l.score)
    gardees: list[Ligne] = []
    for l in lignes:
        if all(iou(l.boite, g.boite) < 0.5 for g in gardees):
            gardees.append(l)
    gardees.sort(key=lambda l: (l.boite[1], l.boite[0]))
    return gardees


# --- étape B (optionnelle) : bulles par RT-DETR ----------------------------------

_rtdetr = None


def _charger_rtdetr():
    global _rtdetr
    if _rtdetr is not None:
        return _rtdetr or None
    try:
        import torch  # noqa: F401
        from transformers import AutoImageProcessor, RTDetrV2ForObjectDetection
        nom = "ogkalu/comic-text-and-bubble-detector"
        _rtdetr = (AutoImageProcessor.from_pretrained(nom), RTDetrV2ForObjectDetection.from_pretrained(nom).eval())
    except Exception:
        _rtdetr = False
    return _rtdetr or None


def detecter_bulles_rtdetr(bande: np.ndarray, progres=lambda *a: None) -> list[list[int]] | None:
    cfg = config()["detection"]
    if cfg["moteur"] == "ocr":
        return None
    modele = _charger_rtdetr()
    if modele is None:
        if cfg["moteur"] == "rtdetr":
            progres("Détecteur RT-DETR indisponible (installe l'option [rtdetr]) : moteur OCR utilisé.")
        return None
    import torch
    from PIL import Image
    proc, net = modele
    ov, H = cfg["chevauchement"], bande.shape[0]
    segs = segments(bande, cfg["hauteur_tuile"])
    morceaux = []  # (boite, t0, t1)
    for i, (y0, y1, hp, bp) in enumerate(segs):
        t0 = y0 if hp else max(0, y0 - ov)
        t1 = y1 if bp else min(H, y1 + ov)
        progres(f"Détection des bulles : tuile {i + 1}/{len(segs)}", (i + 1) / len(segs))
        im = Image.fromarray(bande[t0:t1])
        with torch.no_grad():
            entree = proc(images=im, return_tensors="pt")
            sortie = net(**entree)
        r = proc.post_process_object_detection(sortie, target_sizes=torch.tensor([[im.height, im.width]]),
                                               threshold=0.4)[0]
        for score, lab, box in zip(r["scores"], r["labels"], r["boxes"]):
            if net.config.id2label.get(int(lab), "") != "bubble":
                continue
            x0, y0b, x1, y1b = [int(v) for v in box.tolist()]
            morceaux.append(([x0, y0b + t0, x1, y1b + t0], t0, t1, float(score)))
    return fusionner_coupes(morceaux, cfg["fusion_recouvrement"])


def fusionner_coupes(morceaux, seuil: float) -> list[list[int]]:
    """Réunit les morceaux d'une même bulle coupée entre deux tuiles, puis retire les vrais doublons."""
    boites = [list(m[0]) for m in morceaux]
    touche_bas = [m[0][3] >= m[2] - 4 for m in morceaux]
    touche_haut = [m[0][1] <= m[1] + 4 for m in morceaux]
    fusion = True
    while fusion:
        fusion = False
        for i in range(len(boites)):
            for j in range(len(boites)):
                if i == j or boites[i] is None or boites[j] is None:
                    continue
                a, b = boites[i], boites[j]
                coupe = (touche_bas[i] and touche_haut[j]) and b[1] <= a[3] + 8 and b[1] > a[1]
                if coupe and recouvrement_horizontal(a, b) > seuil:
                    boites[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    touche_bas[i] = touche_bas[j]
                    boites[j] = None
                    fusion = True
    boites = [b for b in boites if b is not None]
    boites.sort(key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
    gardees = []
    for b in boites:
        if all(iou(b, g) < 0.6 for g in gardees):
            gardees.append(b)
    return gardees


# --- étape C : forme des bulles autour des lignes ----------------------------------

def _couleur_anneau(bande: np.ndarray, b: list[int], marge: int = 6) -> tuple[np.ndarray, float]:
    H, W = bande.shape[:2]
    x0, y0, x1, y1 = max(0, b[0] - marge), max(0, b[1] - marge), min(W, b[2] + marge), min(H, b[3] + marge)
    zone = bande[y0:y1, x0:x1].reshape(-1, 3).astype(np.int16)
    m = np.ones((y1 - y0, x1 - x0), bool)
    m[b[1] - y0:b[3] - y0, b[0] - x0:b[2] - x0] = False
    anneau = zone[m.reshape(-1)] if m.any() else zone
    med = np.median(anneau, axis=0)
    return med, float(np.abs(anneau - med).max(axis=1).mean())


def region_autour(bande: np.ndarray, lig: Ligne, boite_guide: list[int] | None = None) -> Region:
    H, W = bande.shape[:2]
    R = 650
    x0, y0 = max(0, lig.boite[0] - R), max(0, lig.boite[1] - R)
    x1, y1 = min(W, lig.boite[2] + R), min(H, lig.boite[3] + R)
    if boite_guide:
        x0, y0 = max(x0, boite_guide[0] - 2), max(y0, boite_guide[1] - 2)
        x1, y1 = min(x1, boite_guide[2] + 2), min(y1, boite_guide[3] + 2)
    zone = bande[y0:y1, x0:x1].astype(np.int16)
    fond, _ = _couleur_anneau(bande, lig.boite)
    proche = (np.abs(zone - fond).max(axis=2) < 30).astype(np.uint8)
    lx0, ly0, lx1, ly1 = lig.boite[0] - x0, lig.boite[1] - y0, lig.boite[2] - x0, lig.boite[3] - y0
    proche[ly0:ly1, lx0:lx1] = 1
    n, lab, stats, _ = cv2.connectedComponentsWithStats(proche, connectivity=4)
    l = lab[(ly0 + ly1) // 2, (lx0 + lx1) // 2]
    comp = (lab == l).astype(np.uint8)
    # bouchage des trous laissés par les lettres
    comp = cv2.morphologyEx(comp, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    plein = np.zeros_like(comp)
    cv2.drawContours(plein, cnts, -1, 1, thickness=cv2.FILLED)
    ys, xs = np.nonzero(plein)
    bx0, by0, bx1, by1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    aire = int(plein.sum())
    aire_ligne = max(1, (ly1 - ly0) * (lx1 - lx0))
    touche = sum([bx0 <= 1, by0 <= 1, bx1 >= plein.shape[1] - 1, by1 >= plein.shape[0] - 1])
    ouverte = (touche >= 2 and not boite_guide) or aire > 60 * aire_ligne or (bx1 - bx0) > 0.97 * W
    fond_t = tuple(int(v) for v in fond)
    if ouverte:
        return _region_libre(lig, fond_t)
    rempl = aire / float((bx1 - bx0) * (by1 - by0))
    typ = "narration" if rempl > 0.9 else "bulle"
    return Region(typ, x0 + bx0, y0 + by0, plein[by0:by1, bx0:bx1].astype(bool), [], fond_t)


def _region_libre(lig: Ligne, fond) -> Region:
    m = 6
    x0, y0, x1, y1 = lig.boite[0] - m, lig.boite[1] - m, lig.boite[2] + m, lig.boite[3] + m
    x0, y0 = max(0, x0), max(0, y0)
    return Region("texte_libre", x0, y0, np.ones((y1 - y0, x1 - x0), bool), [], fond)


def _etendre_libre(r: Region, lig: Ligne) -> None:
    b = r.boite
    m = 6
    nb = [min(b[0], lig.boite[0] - m), min(b[1], lig.boite[1] - m), max(b[2], lig.boite[2] + m), max(b[3], lig.boite[3] + m)]
    nb[0], nb[1] = max(0, nb[0]), max(0, nb[1])
    r.x0, r.y0 = nb[0], nb[1]
    r.masque = np.ones((nb[3] - nb[1], nb[2] - nb[0]), bool)


def _proche_libre(r: Region, lig: Ligne) -> bool:
    b = r.boite
    dy = lig.boite[1] - b[3]
    hmoy = np.median([l.h for l in r.lignes])
    return -hmoy < dy < 1.0 * hmoy and recouvrement_horizontal(b, lig.boite) > 0.3


def construire_regions(bande: np.ndarray, lignes: list[Ligne], bulles_guide: list[list[int]] | None,
                       progres=lambda *a: None) -> list[Region]:
    regions: list[Region] = []
    for i, lig in enumerate(lignes):
        if i % 10 == 0:
            progres(f"Forme des bulles : {i}/{len(lignes)}", i / max(1, len(lignes)))
        cx, cy = lig.centre
        cible = next((r for r in regions if r.type != "texte_libre" and r.contient(cx, cy)), None)
        if cible is None:
            cible = next((r for r in regions if r.type == "texte_libre" and _proche_libre(r, lig)), None)
            if cible is not None:
                _etendre_libre(cible, lig)
        if cible is None:
            guide = None
            if bulles_guide:
                guide = next((g for g in bulles_guide if g[0] <= cx <= g[2] and g[1] <= cy <= g[3]), None)
            cible = region_autour(bande, lig, guide)
            regions.append(cible)
        cible.lignes.append(lig)
    progres("Séparation des bulles liées…", 1.0)
    return separer_liees(regions)


def separer_liees(regions: list[Region]) -> list[Region]:
    """Une bulle formée de plusieurs lobes reliés devient plusieurs bulles du même groupe."""
    sortie, groupe = [], 0
    for r in regions:
        if r.type != "bulle" or len(r.lignes) < 2:
            sortie.append(r)
            continue
        h, w = r.masque.shape
        m8 = r.masque.astype(np.uint8)
        trouve = None
        for ratio in (0.08, 0.14, 0.2, 0.27):
            k = max(7, int(ratio * min(h, w)) | 1)
            erode = cv2.erode(m8, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
            n, lab, stats, _ = cv2.connectedComponentsWithStats(erode, connectivity=4)
            total = max(1, int(erode.sum()))
            grands = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > 0.12 * total]
            porteurs = {}
            for lig in r.lignes:
                cx, cy = lig.centre[0] - r.x0, lig.centre[1] - r.y0
                l = lab[min(h - 1, max(0, cy)), min(w - 1, max(0, cx))]
                if l in grands:
                    porteurs.setdefault(l, []).append(lig)
            if len(porteurs) >= 2 and sum(len(v) for v in porteurs.values()) == len(r.lignes):
                trouve = (lab, porteurs)
                break
            if not porteurs or sum(len(v) for v in porteurs.values()) < len(r.lignes):
                break  # l'érosion avale déjà du texte : bulle simple
        if trouve is None:
            sortie.append(r)
            continue
        lab, porteurs = trouve
        # chaque pixel du masque d'origine va au lobe le plus proche
        inv = np.where(np.isin(lab, list(porteurs)), 0, 1).astype(np.uint8)
        _, plus_proche = cv2.distanceTransformWithLabels(inv, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_CCOMP)
        groupe += 1
        for l, ligs in porteurs.items():
            ys, xs = np.nonzero(lab == l)
            etiquette = plus_proche[ys[0], xs[0]]
            part = r.masque & (plus_proche == etiquette)
            yy, xx = np.nonzero(part)
            sortie.append(Region("bulle", r.x0 + xx.min(), r.y0 + yy.min(),
                                 part[yy.min():yy.max() + 1, xx.min():xx.max() + 1], ligs, r.fond, groupe))
    return sortie


def detecter(bande: np.ndarray, progres=lambda *a: None, cache=None) -> list[Region]:
    moteur_ocr()  # chargement anticipé
    lignes = detecter_lignes(bande, progres, cache)
    guides = detecter_bulles_rtdetr(bande, progres)
    return construire_regions(bande, lignes, guides, progres)
