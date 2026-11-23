# webtoon-fr — v1.1

Traduction de webtoons et de manhwa de l'anglais vers le français, avec une interface web de relecture.

L'outil récupère les images d'un chapitre (par lien ou par import), détecte les bulles, lit le texte anglais,
le fait traduire par un modèle qui **voit les scènes** (pour savoir qui parle à qui), efface l'anglais
et réécrit le français dans la forme de chaque bulle.

## Installation

Python 3.11 ou plus récent.

```bash
cd webtoon-fr
python -m venv .venv
# Windows : .venv\Scripts\activate      macOS / Linux : source .venv/bin/activate
pip install -e .
```

Options :

```bash
pip install -e ".[navigateur]" && playwright install chromium   # pages qui chargent leurs images en JavaScript
pip install -e ".[rtdetr]"                                        # détecteur de bulles RT-DETR (plus précis, lourd : PyTorch)
```

## Lancement

```bash
webtoon-fr web
```

Puis ouvre http://127.0.0.1:8765 dans ton navigateur.

1. **Réglages** : choisis le fournisseur et colle ta clé API. Par défaut : Anthropic, modèle `claude-sonnet-5-5`.
   Un fournisseur compatible OpenAI (OpenAI, LM Studio, Ollama…) marche aussi, à condition que le modèle lise les images.
2. Crée ta série, puis remplis son **glossaire** (noms, termes, qui tutoie qui) et son **contexte** (synopsis, personnages).
3. **Traduire un chapitre** : colle le lien de sa page (un par ligne si le chapitre est sur plusieurs pages),
   dépose ses images, ou importe une **page enregistrée** depuis ton navigateur.
   Avec « Vérifier les images avant de traduire », l'outil montre d'abord les images gardées et écartées :
   tu corriges la sélection, puis tu lances la traduction.
4. **Relecture** : la bande à gauche, les bulles à droite. Corrige une traduction, enregistre (Ctrl+Entrée),
   puis « Mettre à jour les images ». Une bulle corrigée est verrouillée : une nouvelle traduction ne l'écrase jamais.
5. **Télécharger le chapitre** : un zip avec les images et une page `index.html` pour lire le chapitre.

## Récupération des images

| Situation | Ce que fait l'outil |
| --- | --- |
| Images en chargement différé (`data-src`, `srcset`, `<picture>`, `<noscript>`, fond CSS) | Les trouve toutes, dans l'ordre de la page |
| Page qui se remplit en JavaScript | L'ouvre dans un navigateur sans interface (Playwright, ou Chrome / Edge déjà installés), la fait défiler et capte les images |
| Adresses des images seulement dans les données de la page | Les lit dans les scripts |
| Publicités, vignettes, icônes, doublons | Les écarte d'après la largeur des pages du chapitre, avec la raison |
| Espaces blancs entre les cases | Les garde (ils font partie de la bande) |
| Serveur d'images qui exige le Referer ou les cookies de la page | Télécharge avec les deux |
| Serveur qui demande de ralentir (429, 503) | Attend le délai demandé, puis reprend |
| Téléchargement tronqué ou image illisible | Relance, puis signale l'image |
| Image du chapitre impossible à télécharger | S'arrête avec la liste exacte ; une relance ne retélécharge que celles-là |
| Chapitre sur plusieurs pages | Un lien par ligne, dans l'ordre |
| Connexion obligatoire, vérification anti-robot | Ne contourne rien : explique comment enregistrer la page depuis ton navigateur |
| Pages dessinées dans un canvas | Ne contourne pas : demande les fichiers d'origine |

**Page enregistrée** : ouvre le chapitre dans ton navigateur (connecté si besoin), fais défiler jusqu'en bas pour
que toutes les images se chargent, puis Ctrl+S et « Page web complète ». Importe le fichier `.html` avec son dossier
`_files` (bouton « Choisir le dossier des images »), ou un `.zip` des deux.

Chaque récupération écrit `source/rapport.json` : liens, méthode, et pour chaque image son statut et la raison.
Les réglages sont dans la section `[recuperation]` de `config.toml`.

## Ligne de commande

```bash
webtoon-fr run https://…/chapitre-12 --serie mon-manhwa --chapitre 12   # tout faire
webtoon-fr run ./dossier-images --serie mon-manhwa --chapitre 12
webtoon-fr analyze …   # phase 1 : détection, OCR, traduction -> chapitre.json
webtoon-fr render --serie mon-manhwa --chapitre 12                    # phase 2 : effacement, rendu, export
webtoon-fr fetch https://… --serie mon-manhwa --chapitre 12             # récupération seule
webtoon-fr fetch https://…/p1 https://…/p2 --serie mon-manhwa --chapitre 12   # chapitre sur deux pages
webtoon-fr run "Chapitre 12.zip" --serie mon-manhwa --chapitre 12      # page enregistrée
```

## Où sont les fichiers

```
series/mon-manhwa/
├── glossaire.json      noms, termes, honorifiques, registres tu/vous, propositions du traducteur
├── contexte.md         synopsis, personnages, un résumé par chapitre (## Chapitre N)
└── ch-12/
    ├── source/         images gardées (001.jpg…), rapport.json, source.txt (liens), brut/ (tout ce qui a été téléchargé)
    ├── chapitre.json   toutes les bulles : texte, traduction, locuteur, registre, verrouillage…
    ├── historique/     10 dernières versions de chapitre.json
    ├── masques/        forme de chaque bulle
    ├── apercu/         bande originale découpée (comparaison dans l'interface)
    ├── sortie/         images traduites + index.html
    └── cache/          résultats déjà calculés (OCR, traduction)
```

`config.toml` regroupe tous les réglages techniques (tailles de tuiles, seuils, police, taille minimale…).
La clé API est enregistrée dans `reglages.json`, sur ton ordinateur uniquement. Tu peux aussi la passer par la
variable d'environnement `ANTHROPIC_API_KEY` (ou `OPENAI_API_KEY`).

## Modèles

- **OCR** : RapidOCR (modèles PaddleOCR en ONNX), inclus dans le paquet, rien à télécharger.
- **Détection des bulles** : par défaut, à partir des lignes lues par l'OCR et de la forme du fond autour du texte.
  Avec l'option `[rtdetr]`, le détecteur `ogkalu/comic-text-and-bubble-detector` est téléchargé au premier usage.
- **Effacement** : remplissage à la couleur du fond quand il est uni ; sinon LaMa (`lama_fp32.onnx`, environ 200 Mo,
  téléchargé au premier usage dans `~/.cache/webtoon-fr`). Sans connexion, repli sur l'inpainting OpenCV.

## Polices

`fonts/` contient Comic Neue (licence SIL OFL : usage commercial autorisé). Pour changer de police, ajoute le
fichier dans `fonts/`, déclare sa licence dans `fonts/LICENCES.md` avec `commercial: oui`, puis change `police`
dans `config.toml`. L'outil refuse une police absente de ce fichier, et signale les accents manquants.

## Limites de la v1

- Les onomatopées dessinées sont détectées mais laissées intactes (type « Onomatopée » ; tu peux changer le type).
- Les fenêtres système, la cascade OCR complète par type de bulle et le traitement par lots de chapitres
  sont prévus en v2/v3 (voir le document de conception).
- Le résultat dépend de la qualité du scan : relis les bulles marquées « À vérifier ».
