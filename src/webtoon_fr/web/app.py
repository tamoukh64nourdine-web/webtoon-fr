"""API web (FastAPI) + interface. Lancement : `webtoon-fr web` puis http://127.0.0.1:8765"""
from __future__ import annotations

import shutil
import tempfile
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import pipeline
from ..config import chemin, enregistrer_reglages, reglages
from ..export import archive
from ..models import (Chapitre, dossier_chapitre, dossier_serie, ecrire_contexte, ecrire_glossaire, lire_contexte,
                      lire_glossaire, lister_series, nom_valide)

STATIQUE = Path(__file__).parent / "static"
app = FastAPI(title="webtoon-fr")

# --- travaux en arrière-plan (un seul à la fois : le CPU et le LLM sont partagés) -----------

ETAPES = ["recuperation", "recollage", "detection", "ocr", "traduction", "nettoyage", "rendu", "export"]
_executeur = ThreadPoolExecutor(max_workers=1)
_travaux: dict[str, dict] = {}
_verrou = threading.Lock()


def _lancer(type_: str, serie: str, numero: str, fonction) -> dict:
    tid = uuid.uuid4().hex[:10]
    t = {"id": tid, "type": type_, "serie": serie, "chapitre": numero, "etat": "en_attente", "etape": None,
         "message": "En attente…", "fraction": None, "journal": [], "erreur": None, "debut": time.time()}
    with _verrou:
        _travaux[tid] = t

    def progres(etape, message, fraction=None):
        with _verrou:
            t["etape"], t["message"], t["fraction"] = etape, message, fraction
            if fraction is None or fraction >= 1.0 or not t["journal"] or t["journal"][-1]["etape"] != etape:
                t["journal"].append({"etape": etape, "message": message, "t": round(time.time() - t["debut"], 1)})

    def corps():
        t["etat"] = "en_cours"
        try:
            fonction(progres)
            t["etat"], t["message"] = "termine", "Terminé."
        except Exception as e:  # l'erreur est montrée telle quelle dans l'interface
            t["etat"], t["erreur"] = "erreur", str(e) or e.__class__.__name__
            t["trace"] = traceback.format_exc()[-3000:]
        t["fin"] = time.time()

    _executeur.submit(corps)
    return t


@app.get("/api/travaux/{tid}")
def travail(tid: str):
    if tid not in _travaux:
        raise HTTPException(404, "Travail inconnu.")
    return _travaux[tid]


@app.get("/api/travaux")
def travaux():
    return sorted(_travaux.values(), key=lambda t: -t["debut"])[:20]


# --- séries ----------------------------------------------------------------------------

class NouvelleSerie(BaseModel):
    nom: str


@app.get("/api/series")
def series():
    return lister_series()


@app.post("/api/series")
def creer_serie(s: NouvelleSerie):
    try:
        nom = nom_valide(s.nom)
    except ValueError:
        raise HTTPException(400, "Donne un nom de série avec au moins une lettre ou un chiffre.")
    dossier_serie(nom).mkdir(parents=True, exist_ok=True)
    if not (dossier_serie(nom) / "glossaire.json").exists():
        ecrire_glossaire(nom, lire_glossaire(nom))
    return {"serie": nom}


@app.get("/api/series/{serie}/glossaire")
def get_glossaire(serie: str):
    return lire_glossaire(serie)


@app.put("/api/series/{serie}/glossaire")
def put_glossaire(serie: str, g: dict):
    ecrire_glossaire(serie, g)
    return lire_glossaire(serie)


class Texte(BaseModel):
    texte: str


@app.get("/api/series/{serie}/contexte")
def get_contexte(serie: str):
    return {"texte": lire_contexte(serie)}


@app.put("/api/series/{serie}/contexte")
def put_contexte(serie: str, t: Texte):
    ecrire_contexte(serie, t.texte)
    return {"texte": t.texte}


# --- chapitres ---------------------------------------------------------------------------

@app.post("/api/series/{serie}/chapitres")
async def nouveau_chapitre(serie: str, numero: str = Form(...), url: str = Form(""),
                           images: list[UploadFile] | None = File(None)):
    serie = nom_valide(serie)
    numero = numero.strip()
    if not numero:
        raise HTTPException(400, "Indique le numéro du chapitre.")
    url = url.strip()
    fichiers = []
    if not url and not images:
        raise HTTPException(400, "Colle le lien de la page du chapitre ou ajoute ses images.")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Le lien doit commencer par http:// ou https://.")
    tmp = None
    if not url:
        tmp = Path(tempfile.mkdtemp(prefix="webtoon-fr-"))
        for i, im in enumerate(images or []):
            nom = Path(im.filename or f"{i:03d}.png").name
            if Path(nom).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            (tmp / nom).write_bytes(await im.read())
            fichiers.append(tmp / nom)
        if not fichiers:
            raise HTTPException(400, "Aucune image JPG, PNG ou WebP reçue.")

    def faire(progres):
        try:
            pipeline.tout(serie, numero, url or None, fichiers or None, progres)
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    return _lancer("complet", serie, numero, faire)


@app.get("/api/series/{serie}/chapitres/{numero}")
def chapitre(serie: str, numero: str):
    try:
        c = Chapitre.charger(serie, numero)
    except FileNotFoundError:
        raise HTTPException(404, "Chapitre introuvable.")
    d = c.to_dict()
    sortie = c.dossier / "sortie"
    d["images"] = sorted(f.name for f in sortie.glob("[0-9][0-9][0-9].*")) if sortie.exists() else []
    d["apercus"] = sorted(f.name for f in (c.dossier / "apercu").glob("*.jpg")) if (c.dossier / "apercu").exists() else []
    d["base"] = f"/fichiers/{nom_valide(serie)}/{c.dossier.name}"
    return d


class ModifBulle(BaseModel):
    francais: str | None = None
    taille_police: int | None = None
    effacer_taille: bool = False
    verrouille: bool | None = None
    a_verifier: bool | None = None
    locuteur: str | None = None
    destinataire: str | None = None
    registre: str | None = None
    type: str | None = None
    anglais_corrige: str | None = None


@app.put("/api/series/{serie}/chapitres/{numero}/bulles/{bid}")
def modifier_bulle(serie: str, numero: str, bid: int, m: ModifBulle):
    c = Chapitre.charger(serie, numero)
    try:
        b = c.bulle(bid)
    except KeyError:
        raise HTTPException(404, "Bulle introuvable.")
    if m.francais is not None and m.francais != b.francais:
        b.francais = m.francais
        b.verrouille = True  # une correction manuelle n'est plus jamais écrasée
    if m.effacer_taille:
        b.taille_police = None
    elif m.taille_police is not None:
        b.taille_police = max(6, min(120, m.taille_police))
    for champ in ("locuteur", "destinataire", "anglais_corrige"):
        v = getattr(m, champ)
        if v is not None:
            setattr(b, champ, v or None)
    if m.registre is not None:
        b.registre = m.registre if m.registre in ("tu", "vous") else None
    if m.type in ("bulle", "narration", "texte_libre", "onomatopee"):
        b.type = m.type
    if m.verrouille is not None:
        b.verrouille = m.verrouille
    if m.a_verifier is not None:
        b.a_verifier = m.a_verifier
        if not m.a_verifier:
            b.notes = []
    c.sauver(historiser=True)
    return c.to_dict()["bulles"][[x.id for x in c.bulles].index(bid)]


@app.post("/api/series/{serie}/chapitres/{numero}/rendu")
def relancer_rendu(serie: str, numero: str):
    return _lancer("rendu", serie, numero, lambda p: pipeline.rendre_chapitre(serie, numero, p))


@app.post("/api/series/{serie}/chapitres/{numero}/traduction")
def relancer_traduction(serie: str, numero: str):
    def faire(p):
        c = Chapitre.charger(serie, numero)
        pipeline.traduire_chapitre(c, None, p)
        pipeline.rendre_chapitre(serie, numero, p)
    return _lancer("traduction", serie, numero, faire)


@app.post("/api/series/{serie}/chapitres/{numero}/analyse")
def relancer_analyse(serie: str, numero: str):
    return _lancer("complet", serie, numero, lambda p: pipeline.tout(serie, numero, None, None, p))


@app.get("/api/series/{serie}/chapitres/{numero}/archive")
def telecharger(serie: str, numero: str):
    d = dossier_chapitre(serie, numero)
    if not (d / "sortie").exists():
        raise HTTPException(404, "Aucune image exportée pour ce chapitre.")
    z = archive(d)
    return FileResponse(z, filename=z.name, media_type="application/zip")


# --- réglages ------------------------------------------------------------------------------

class Reglages(BaseModel):
    fournisseur: str | None = None
    modele: str | None = None
    cle_api: str | None = None
    url_base: str | None = None
    multimodal: bool | None = None


def _masquer(r: dict) -> dict:
    r = dict(r)
    k = r.get("cle_api") or ""
    r["cle_api"] = ""
    r["cle_api_definie"] = bool(k)
    r["cle_api_fin"] = k[-4:] if len(k) > 8 else ""
    return r


@app.get("/api/reglages")
def get_reglages():
    return _masquer(reglages())


@app.put("/api/reglages")
def put_reglages(r: Reglages):
    d = r.model_dump()
    if not d.get("cle_api"):
        d.pop("cle_api")  # champ vide = on garde la clé actuelle
    if d.get("fournisseur") not in (None, "anthropic", "openai"):
        raise HTTPException(400, "Fournisseur inconnu.")
    return _masquer(enregistrer_reglages(d))


# --- fichiers statiques ------------------------------------------------------------------------

chemin("series").mkdir(parents=True, exist_ok=True)
app.mount("/fichiers", StaticFiles(directory=chemin("series")), name="fichiers")
app.mount("/polices", StaticFiles(directory=chemin("fonts")), name="polices")
app.mount("/static", StaticFiles(directory=STATIQUE), name="static")


@app.get("/")
def accueil():
    return FileResponse(STATIQUE / "index.html")
