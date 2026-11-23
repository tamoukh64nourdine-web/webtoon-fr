# Historique des versions

## v1.1.0 — 23 novembre 2026

Récupération des images refaite.

- Trouve les images déclarées de toutes les façons courantes : `data-*` de chargement différé, `srcset`, `<picture>`, `<noscript>`, fond CSS, et adresses écrites dans les données JSON de la page.
- Navigateur sans interface amélioré : défilement progressif jusqu'en bas, capture des images au passage, images dans les iframes ; utilise Chrome ou Edge déjà installés si Playwright n'a pas son navigateur.
- Filtre par largeur dominante au lieu du poids : les publicités et vignettes sont écartées, les espaces blancs ne sont plus supprimés (correction).
- Téléchargement avec les cookies et le Referer de la page, respect de `Retry-After` (429, 503), détection des téléchargements tronqués.
- Images manquantes : arrêt avec la liste exacte au lieu d'un trou dans le chapitre ; une relance ne retélécharge que ce qui manque.
- Diagnostic clair des pages anti-robot, des pages de connexion et des pages en canvas, sans contournement.
- Chapitre sur plusieurs pages : plusieurs liens, un par ligne.
- Nouvelle source « Page enregistrée » : .html + dossier d'images, ou .zip.
- Nouvelle étape « Vérifier les images » : vignettes dans l'ordre, statut et raison, garder ou écarter, relancer, puis traduire.
- Rapport détaillé `source/rapport.json`.

## v1.0.1 — 23 novembre 2026

- Correction : les connexions HTTPS utilisent les certificats du système (Windows, macOS). Corrige l'erreur `CERTIFICATE_VERIFY_FAILED` quand un antivirus ou un proxy d'entreprise inspecte les connexions. La vérification reste active.

## v1.0.0 — 23 novembre 2026

Première version.

- Récupération d'un chapitre par lien (images chargées au défilement, repli sur un navigateur sans interface) ou par import d'images.
- Pipeline complet : recollage, détection du texte et des bulles par tuiles, OCR anglais avec relecture des zones peu sûres, ordre de lecture, séparation des bulles liées.
- Traduction par un LLM qui voit les scènes (bulles numérotées), avec glossaire de la série, résumés des chapitres précédents, contrôle anti-décalage, essais espacés et cache.
- Effacement du texte anglais (remplissage ou LaMa), écriture du français dans la forme réelle des bulles, taille minimale lisible, typographie française, onomatopées laissées intactes.
- Corrections manuelles verrouillées et historique des versions de chaque chapitre.
- Interface web : séries, chapitres, suivi des étapes, relecture bulle par bulle, glossaire, contexte, réglages.
