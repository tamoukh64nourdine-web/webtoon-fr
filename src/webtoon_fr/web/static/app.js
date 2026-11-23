"use strict";

/* ---------- utilitaires ---------- */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const vue = $("#vue");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;

async function api(methode, chemin, corps) {
  const opts = { method: methode, headers: {} };
  if (corps instanceof FormData) opts.body = corps;
  else if (corps !== undefined) { opts.body = JSON.stringify(corps); opts.headers["Content-Type"] = "application/json"; }
  const r = await fetch(chemin, opts);
  let d = null;
  try { d = await r.json(); } catch { /* réponse vide */ }
  if (!r.ok) throw new Error((d && (typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail))) || `Erreur ${r.status}`);
  return d;
}

let minuteurToast;
function toast(msg, erreur = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("erreur", erreur);
  t.classList.add("visible");
  clearTimeout(minuteurToast);
  minuteurToast = setTimeout(() => t.classList.remove("visible"), erreur ? 6000 : 2800);
}

const ETATS = {
  nouveau: ["Nouveau", ""], analyse: ["Analysé, non traduit", "attention"], traduit: ["Traduit, images à générer", "attention"],
  rendu: ["Prêt", "ok"], erreur: ["Erreur", "erreur"],
};
const TYPES = { bulle: "Bulle", narration: "Narration", texte_libre: "Texte libre", onomatopee: "Onomatopée (non traduite)" };
const ETAPES = [
  ["recuperation", "Récupération des images"], ["recollage", "Recollage en une bande"], ["detection", "Détection du texte et des bulles"],
  ["ocr", "Lecture du texte anglais"], ["traduction", "Traduction"], ["nettoyage", "Effacement du texte anglais"],
  ["rendu", "Écriture du texte français"], ["export", "Export des images"],
];
const dateCourte = (t) => t ? new Date(t * 1000).toLocaleString("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";

/* ---------- navigation ---------- */
let pollers = [];
function arreterPollers() { pollers.forEach(clearInterval); pollers = []; }

function nav(serie, actif) {
  const n = $("#nav");
  if (!serie) { n.innerHTML = ""; return; }
  const lien = (h, t, k) => `<a class="nav-lien" href="${h}" ${actif === k ? 'aria-current="page"' : ""}>${t}</a>`;
  n.innerHTML = `<span class="nav-lien nav-serie">${esc(serie)}</span>` +
    lien(`#/serie/${enc(serie)}`, "Chapitres", "chapitres") +
    lien(`#/serie/${enc(serie)}/glossaire`, "Glossaire", "glossaire") +
    lien(`#/serie/${enc(serie)}/contexte`, "Contexte", "contexte");
}

async function router() {
  arreterPollers();
  const p = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  $(".nav-reglages").toggleAttribute("aria-current", p[0] === "reglages");
  try {
    if (p[0] === "serie" && p[1] && p[2] === "ch" && p[3]) await vueChapitre(p[1], p[3]);
    else if (p[0] === "serie" && p[1] && p[2] === "glossaire") await vueGlossaire(p[1]);
    else if (p[0] === "serie" && p[1] && p[2] === "contexte") await vueContexte(p[1]);
    else if (p[0] === "serie" && p[1]) await vueSerie(p[1]);
    else if (p[0] === "reglages") await vueReglages();
    else await vueAccueil();
  } catch (e) {
    vue.innerHTML = `<div class="vide"><h2>Impossible d'afficher cette page</h2><p>${esc(e.message)}</p><p><a class="btn" href="#/">Retour à l'accueil</a></p></div>`;
  }
  vue.focus({ preventScroll: true });
}
window.addEventListener("hashchange", router);

/* ---------- accueil ---------- */
async function vueAccueil() {
  nav(null);
  const series = await api("GET", "/api/series");
  const formulaire = `
    <form id="f-serie" class="panneau">
      <h2>${series.length ? "Nouvelle série" : "Crée ta première série"}</h2>
      <label class="champ"><span>Nom de la série</span>
        <input type="text" name="nom" required placeholder="Le nom de ton manhwa" autocomplete="off"></label>
      <button class="btn btn-plein btn-large">Créer la série</button>
      <p class="aide">Chaque série a son glossaire (noms, termes, tutoiement) et son fichier de contexte, partagés par tous ses chapitres.</p>
    </form>`;
  if (!series.length) {
    vue.innerHTML = `<h1>Traduire un webtoon</h1>
      <p class="sous-titre">De l'anglais vers le français : l'outil récupère les images, lit les bulles, traduit selon l'intrigue et réécrit le texte dans chaque bulle.</p>
      <div style="max-width:440px">${formulaire}</div>`;
  } else {
    vue.innerHTML = `<h1>Tes séries</h1><p class="sous-titre">Choisis une série pour traduire un chapitre ou relire une traduction.</p>
      <div class="grille-serie">
        <div>${formulaire}</div>
        <table class="tableau"><thead><tr><th>Série</th><th>Chapitres</th><th>À vérifier</th></tr></thead><tbody>
        ${series.map((s) => `<tr data-href="#/serie/${enc(s.serie)}"><td><a href="#/serie/${enc(s.serie)}">${esc(s.serie)}</a></td>
          <td class="num">${s.chapitres.length}</td>
          <td class="num">${s.chapitres.reduce((a, c) => a + c.a_verifier, 0) || "Aucune"}</td></tr>`).join("")}
        </tbody></table>
      </div>`;
    lignesCliquables();
  }
  $("#f-serie").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const r = await api("POST", "/api/series", { nom: new FormData(ev.target).get("nom") });
      location.hash = `#/serie/${enc(r.serie)}`;
    } catch (e) { toast(e.message, true); }
  });
}

function lignesCliquables() {
  $$("tr[data-href]").forEach((tr) => tr.addEventListener("click", (e) => { if (e.target.tagName !== "A") location.hash = tr.dataset.href; }));
}

/* ---------- série : chapitres + nouveau chapitre ---------- */
async function vueSerie(serie) {
  nav(serie, "chapitres");
  const series = await api("GET", "/api/series");
  const s = series.find((x) => x.serie === serie);
  if (!s) throw new Error("Cette série n'existe pas.");
  const travaux = await api("GET", "/api/travaux");
  const enCours = travaux.find((t) => t.serie === serie && ["en_attente", "en_cours"].includes(t.etat));
  const suivant = s.chapitres.length ? String(Math.max(...s.chapitres.map((c) => parseFloat(c.numero) || 0)) + 1) : "1";

  vue.innerHTML = `
    <h1>${esc(serie)}</h1>
    <p class="sous-titre">Ajoute un chapitre par son lien ou ses images. La traduction tient compte du glossaire et du contexte de la série.</p>
    <div class="grille-serie">
      <div class="pile">
        <form id="f-chap" class="panneau" ${enCours ? "hidden" : ""}>
          <h2>Traduire un chapitre</h2>
          <label class="champ"><span>Numéro du chapitre</span><input type="text" name="numero" value="${esc(suivant)}" required inputmode="decimal"></label>
          <div class="bascule" role="group" aria-label="Source du chapitre">
            <button type="button" data-src="url" aria-pressed="true">Lien de la page</button>
            <button type="button" data-src="images" aria-pressed="false">Images</button>
          </div>
          <label class="champ" id="zone-url"><span>Lien de la page du chapitre</span>
            <input type="url" name="url" placeholder="https://…" autocomplete="off">
            <p class="aide">Pour récupérer tes propres chapitres publiés. Si la page demande une connexion, ajoute plutôt les images.</p></label>
          <div id="zone-images" hidden>
            <label class="depot" id="depot" tabindex="0">
              <input type="file" name="images" accept=".jpg,.jpeg,.png,.webp" multiple hidden>
              <span><strong>Dépose les images ici</strong><br>ou clique pour les choisir. Elles sont lues dans l'ordre de leur nom.</span>
            </label>
            <p class="aide" id="liste-fichiers"></p>
          </div>
          <button class="btn btn-plein btn-large" style="margin-top:8px">Traduire le chapitre</button>
        </form>
        <section class="panneau suivi" id="suivi" ${enCours ? "" : "hidden"} aria-live="polite"></section>
      </div>
      <div>
        ${s.chapitres.length ? `<table class="tableau"><thead><tr><th>Chapitre</th><th>État</th><th>Bulles</th><th>À vérifier</th><th>Modifié</th></tr></thead><tbody>
          ${s.chapitres.slice().reverse().map((c) => {
            const [lib, cl] = ETATS[c.etat] || [c.etat, ""];
            const h = `#/serie/${enc(serie)}/ch/${enc(c.numero)}`;
            return `<tr data-href="${h}"><td><a href="${h}">Chapitre ${esc(c.numero)}</a></td><td><span class="etiquette ${cl}">${lib}</span></td>
              <td class="num">${c.bulles}</td><td class="num">${c.a_verifier ? `<span class="etiquette attention">${c.a_verifier}</span>` : "Aucune"}</td>
              <td>${dateCourte(c.maj)}</td></tr>`;
          }).join("")}</tbody></table>`
        : `<div class="vide"><h2>Aucun chapitre pour l'instant</h2><p>Colle le lien de ton premier chapitre ou ajoute ses images pour lancer la traduction.</p></div>`}
      </div>
    </div>`;
  lignesCliquables();

  // source : lien ou images
  const form = $("#f-chap");
  let source = "url";
  let fichiers = [];
  $$(".bascule button", form).forEach((b) => b.addEventListener("click", () => {
    source = b.dataset.src;
    $$(".bascule button", form).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    $("#zone-url").hidden = source !== "url";
    $("#zone-images").hidden = source !== "images";
  }));
  const depot = $("#depot"), entree = $("input[type=file]", depot);
  const majFichiers = (fs) => {
    fichiers = [...fs].filter((f) => /\.(jpe?g|png|webp)$/i.test(f.name)).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    $("#liste-fichiers").textContent = fichiers.length ? `${fichiers.length} image(s) : ${fichiers.slice(0, 4).map((f) => f.name).join(", ")}${fichiers.length > 4 ? "…" : ""}` : "";
  };
  entree.addEventListener("change", () => majFichiers(entree.files));
  depot.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); entree.click(); } });
  ["dragenter", "dragover"].forEach((t) => depot.addEventListener(t, (e) => { e.preventDefault(); depot.classList.add("survol"); }));
  ["dragleave", "drop"].forEach((t) => depot.addEventListener(t, (e) => { e.preventDefault(); depot.classList.remove("survol"); }));
  depot.addEventListener("drop", (e) => majFichiers(e.dataTransfer.files));

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const fd = new FormData();
    fd.append("numero", form.numero.value);
    if (source === "url") {
      if (!form.url.value.trim()) return toast("Colle le lien de la page du chapitre.", true);
      fd.append("url", form.url.value.trim());
    } else {
      if (!fichiers.length) return toast("Ajoute au moins une image.", true);
      fichiers.forEach((f) => fd.append("images", f, f.name));
    }
    try {
      const t = await api("POST", `/api/series/${enc(serie)}/chapitres`, fd);
      form.hidden = true;
      suivre(t.id, $("#suivi"), () => router());
    } catch (e) { toast(e.message, true); }
  });

  if (enCours) suivre(enCours.id, $("#suivi"), () => router());
}

/* suivi d'un travail en arrière-plan */
function suivre(tid, el, fini) {
  el.hidden = false;
  const dessiner = (t) => {
    const idx = ETAPES.findIndex(([k]) => k === t.etape);
    const titre = t.etat === "erreur" ? "La traduction s'est arrêtée" : t.etat === "termine" ? "Chapitre traduit" : `Chapitre ${esc(t.chapitre)} en cours`;
    const etapes = t.type === "rendu" ? ETAPES.slice(5) : t.type === "traduction" ? ETAPES.slice(4) : ETAPES;
    el.innerHTML = `<h2>${titre}</h2>
      <ol>${etapes.map(([k, lib]) => {
        const i = ETAPES.findIndex(([x]) => x === k);
        const cl = t.etat === "termine" || i < idx ? "fait" : i === idx && t.etat !== "erreur" ? "actif" : "";
        return `<li class="${cl}"><span class="pt"></span>${lib}</li>`;
      }).join("")}</ol>
      ${t.etat === "en_cours" || t.etat === "en_attente" ? `<div class="jauge"><div style="width:${Math.round((t.fraction ?? 0) * 100)}%"></div></div>` : ""}
      <p class="message-travail">${esc(t.etat === "termine" ? "" : t.message)}</p>
      ${t.erreur ? `<div class="boite-erreur">${esc(t.erreur)}</div><p><button class="btn" id="reprendre" type="button">Fermer</button></p>` : ""}
      ${t.etat === "termine" ? `<p><a class="btn btn-plein btn-large" href="#/serie/${enc(t.serie)}/ch/${enc(t.chapitre)}">Relire le chapitre ${esc(t.chapitre)}</a></p>` : ""}`;
    const b = $("#reprendre", el);
    if (b) b.addEventListener("click", fini);
  };
  const tick = async () => {
    try {
      const t = await api("GET", `/api/travaux/${tid}`);
      dessiner(t);
      if (t.etat === "termine" || t.etat === "erreur") {
        arreterPollers();
        if (t.etat === "termine") toast(`Chapitre ${t.chapitre} terminé.`);
      }
    } catch (e) { arreterPollers(); el.innerHTML = `<div class="boite-erreur">${esc(e.message)}</div>`; }
  };
  tick();
  pollers.push(setInterval(tick, 1000));
}

/* ---------- relecture d'un chapitre ---------- */
async function vueChapitre(serie, numero) {
  nav(serie, "chapitres");
  let d = await api("GET", `/api/series/${enc(serie)}/chapitres/${enc(numero)}`);
  let mode = d.images.length ? "traduit" : "original";
  let filtre = false;
  let active = null;
  let aRendre = false;
  const H = d.source.hauteur, W = d.source.largeur;
  const racine = `/api/series/${enc(serie)}/chapitres/${enc(numero)}`;

  const classe = (b) => b.type === "onomatopee" ? "inactif" : b.a_verifier ? "attention" : b.verrouille ? "verrou" : "";
  const nVerif = () => d.bulles.filter((b) => b.a_verifier).length;

  vue.innerHTML = `
    <div class="entete-chapitre">
      <div><p class="fil"><a href="#/serie/${enc(serie)}">${esc(serie)}</a> / chapitre ${esc(numero)}</p>
        <h1>Chapitre ${esc(numero)}</h1></div>
      <div class="actions">
        <button class="btn" id="b-retraduire" type="button">Retraduire</button>
        <button class="btn" id="b-rendu" type="button">Mettre à jour les images</button>
        <a class="btn" id="b-lecteur" href="${d.base}/sortie/index.html" target="_blank" rel="noopener">Ouvrir le lecteur</a>
        <a class="btn btn-plein" id="b-zip" href="${racine}/archive">Télécharger le chapitre</a>
      </div>
    </div>
    <div class="bandeau" id="bandeau" hidden></div>
    <section class="panneau suivi" id="suivi-chap" hidden aria-live="polite" style="margin-bottom:16px"></section>
    <div class="barre-outils">
      <div class="bascule" role="group" aria-label="Affichage de la bande">
        <button type="button" data-mode="traduit">Traduit</button><button type="button" data-mode="original">Original</button>
      </div>
      <label class="case"><input type="checkbox" id="filtre"> <span id="lib-filtre"></span></label>
    </div>
    <div class="relecture">
      <div class="bande-cadre" id="cadre"><div class="bande" id="bande"></div></div>
      <div class="liste-bulles" id="liste"></div>
    </div>`;

  const bande = $("#bande"), liste = $("#liste"), cadre = $("#cadre");
  const majOutils = () => {
    $$(".bascule button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
    $(".bascule button[data-mode=traduit]").disabled = !d.images.length;
    $("#lib-filtre").textContent = `Seulement les bulles à vérifier (${nVerif()})`;
    $("#b-lecteur").hidden = $("#b-zip").hidden = !d.images.length;
    const bd = $("#bandeau");
    if (aRendre) {
      bd.hidden = false;
      bd.innerHTML = `<span>Modifications enregistrées. Mets à jour les images pour les voir dans la bande.</span><button class="btn" type="button" id="b-rendu2">Mettre à jour les images</button>`;
      $("#b-rendu2").addEventListener("click", lancerRendu);
    } else if (d.etat === "analyse") {
      bd.hidden = false;
      bd.innerHTML = `<span>Ce chapitre n'est pas encore traduit. Vérifie la clé API dans Réglages, puis lance la traduction.</span><button class="btn" type="button" id="b-trad2">Traduire</button>`;
      $("#b-trad2").addEventListener("click", lancerTraduction);
    } else bd.hidden = true;
  };

  const dessinerBande = () => {
    const v = Math.round(d.maj || 0);
    const imgs = mode === "traduit" ? d.images.map((n) => `${d.base}/sortie/${n}?v=${v}`) : d.apercus.map((n) => `${d.base}/apercu/${n}?v=${v}`);
    bande.innerHTML = imgs.length ? imgs.map((s) => `<img src="${s}" alt="" loading="lazy">`).join("") : `<p style="color:#ccd;padding:20px">Aucune image générée pour l'instant.</p>`;
    bande.insertAdjacentHTML("beforeend", d.bulles.map((b) =>
      `<button class="repere ${classe(b)}" type="button" data-id="${b.id}" style="top:${(b.boite[1] / H) * 100}%;left:${(b.boite[0] / W) * 100}%"
        aria-label="Bulle ${b.id}">${b.id}</button>`).join("") + `<div class="cadre-bulle" id="cadre-bulle" hidden></div>`);
    $$(".repere", bande).forEach((r) => r.addEventListener("click", () => activer(+r.dataset.id, "liste")));
  };

  const ligne = (b) => {
    const qui = [b.locuteur, b.destinataire].filter(Boolean);
    return `<article class="ligne-bulle ${classe(b)} ${active === b.id ? "active" : ""}" id="bulle-${b.id}" data-id="${b.id}">
      <button class="pastille" type="button" aria-label="Voir la bulle ${b.id} dans la bande">${b.id}</button>
      <div>
        <div class="meta"><span>${TYPES[b.type] || b.type}</span>
          ${qui.length ? `<span>${esc(qui[0])}${qui[1] ? ` à ${esc(qui[1])}` : ""}</span>` : ""}
          ${b.registre ? `<span class="etiquette">${b.registre === "tu" ? "Tutoiement" : "Vouvoiement"}</span>` : ""}
          ${b.verrouille ? `<span class="etiquette ok">Verrouillée</span>` : ""}
          ${b.a_verifier ? `<span class="etiquette attention">À vérifier</span>` : ""}</div>
        <p class="anglais">${b.anglais_corrige ? `<del>${esc(b.anglais)}</del> ${esc(b.anglais_corrige)}` : esc(b.anglais)}</p>
        ${b.type === "onomatopee" ? `<p class="aide">Les onomatopées dessinées sont laissées intactes. Change le type pour la traduire.</p>` :
          `<textarea class="francais" rows="2" aria-label="Traduction de la bulle ${b.id}">${esc(b.francais)}</textarea>`}
        ${b.notes && b.notes.length ? `<ul class="notes">${b.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
        <div class="commandes-bulle">
          <label>Type <select class="c-type">${Object.entries(TYPES).map(([k, v]) => `<option value="${k}" ${k === b.type ? "selected" : ""}>${v.replace(" (non traduite)", "")}</option>`).join("")}</select></label>
          <label>Taille <input class="c-taille" type="number" min="6" max="120" placeholder="auto" value="${b.taille_police ?? ""}"></label>
          <label class="case"><input class="c-verrou" type="checkbox" ${b.verrouille ? "checked" : ""}> Verrouillée</label>
          <span class="pousse">
            ${b.a_verifier ? `<button class="btn btn-discret c-ok" type="button">Marquer comme vérifiée</button>` : ""}
            <button class="btn c-save" type="button" hidden>Enregistrer</button>
          </span>
        </div>
      </div></article>`;
  };

  const dessinerListe = () => {
    const bs = filtre ? d.bulles.filter((b) => b.a_verifier) : d.bulles;
    liste.innerHTML = bs.length ? bs.map(ligne).join("") : `<div class="vide" style="border:0">${filtre ? "Aucune bulle à vérifier." : "Aucune bulle détectée dans ce chapitre."}</div>`;
    $$(".ligne-bulle", liste).forEach(brancherLigne);
  };

  const remplacer = (b) => {
    d.bulles[d.bulles.findIndex((x) => x.id === b.id)] = b;
    const el = $(`#bulle-${b.id}`);
    if (el) { el.outerHTML = ligne(b); brancherLigne($(`#bulle-${b.id}`)); }
    const r = $(`.repere[data-id="${b.id}"]`);
    if (r) r.className = `repere ${classe(b)}`;
    majOutils();
  };

  function brancherLigne(el) {
    const id = +el.dataset.id;
    const b = () => d.bulles.find((x) => x.id === id);
    const save = $(".c-save", el), ta = $("textarea", el), taille = $(".c-taille", el), type = $(".c-type", el);
    const sale = () => { save.hidden = false; };
    $(".pastille", el).addEventListener("click", () => activer(id, "bande"));
    el.addEventListener("focusin", () => { if (active !== id) activer(id, "bande", false); });
    if (ta) { ta.addEventListener("input", sale); ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); enregistrer(); } }); }
    taille.addEventListener("input", sale);
    type.addEventListener("change", sale);
    const enregistrer = async () => {
      const corps = { type: type.value };
      if (ta && ta.value !== b().francais) corps.francais = ta.value;
      if (taille.value === "") corps.effacer_taille = true; else corps.taille_police = parseInt(taille.value, 10);
      try { remplacer(await api("PUT", `${racine}/bulles/${id}`, corps)); aRendre = true; majOutils(); toast(`Bulle ${id} enregistrée.`); }
      catch (e) { toast(e.message, true); }
    };
    save.addEventListener("click", enregistrer);
    $(".c-verrou", el).addEventListener("change", async (e) => {
      try { remplacer(await api("PUT", `${racine}/bulles/${id}`, { verrouille: e.target.checked })); } catch (er) { toast(er.message, true); }
    });
    const ok = $(".c-ok", el);
    if (ok) ok.addEventListener("click", async () => {
      try { remplacer(await api("PUT", `${racine}/bulles/${id}`, { a_verifier: false })); toast(`Bulle ${id} vérifiée.`); } catch (er) { toast(er.message, true); }
    });
  }

  function activer(id, defiler, defilerListe = true) {
    active = id;
    $$(".ligne-bulle").forEach((l) => l.classList.toggle("active", +l.dataset.id === id));
    const b = d.bulles.find((x) => x.id === id);
    const c = $("#cadre-bulle");
    if (b && c) {
      c.hidden = false;
      Object.assign(c.style, { top: `${(b.boite[1] / H) * 100}%`, left: `${(b.boite[0] / W) * 100}%`,
        width: `${((b.boite[2] - b.boite[0]) / W) * 100}%`, height: `${((b.boite[3] - b.boite[1]) / H) * 100}%` });
      if (defiler === "bande" || window.innerWidth > 900) {
        const y = (b.boite[1] / H) * bande.offsetHeight - cadre.clientHeight / 3;
        cadre.scrollTo({ top: Math.max(0, y), behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      }
    }
    if (defiler === "liste" && defilerListe) {
      const l = $(`#bulle-${id}`);
      if (l) { l.scrollIntoView({ block: "center" }); const t = $("textarea", l); if (t) t.focus({ preventScroll: true }); }
    }
  }

  async function recharger() {
    d = await api("GET", `/api/series/${enc(serie)}/chapitres/${enc(numero)}`);
    if (d.images.length && mode === "original" && !aRendre) mode = "traduit";
    majOutils(); dessinerBande(); dessinerListe();
  }

  async function lancer(chemin, quoi) {
    try {
      const t = await api("POST", `${racine}/${chemin}`);
      const s = $("#suivi-chap");
      suivre(t.id, s, () => { s.hidden = true; });
      const attente = setInterval(async () => {
        const e = await api("GET", `/api/travaux/${t.id}`);
        if (e.etat === "termine") { clearInterval(attente); aRendre = false; await recharger(); setTimeout(() => { s.hidden = true; }, 1500); }
        if (e.etat === "erreur") clearInterval(attente);
      }, 1200);
      pollers.push(attente);
      toast(`${quoi} lancée.`);
    } catch (e) { toast(e.message, true); }
  }
  const lancerRendu = () => lancer("rendu", "Mise à jour des images");
  const lancerTraduction = () => lancer("traduction", "Traduction");

  $("#b-rendu").addEventListener("click", lancerRendu);
  $("#b-retraduire").addEventListener("click", () => {
    const n = d.bulles.filter((b) => b.verrouille).length;
    if (confirm(`Retraduire tout le chapitre ? ${n ? `Les ${n} bulle(s) verrouillée(s) seront conservées.` : "Aucune bulle n'est verrouillée : toutes les traductions seront remplacées."}`)) lancerTraduction();
  });
  $$(".bascule button").forEach((b) => b.addEventListener("click", () => { mode = b.dataset.mode; majOutils(); dessinerBande(); if (active) activer(active, null); }));
  $("#filtre").addEventListener("change", (e) => { filtre = e.target.checked; dessinerListe(); });

  majOutils(); dessinerBande(); dessinerListe();
  const travaux = await api("GET", "/api/travaux");
  const t = travaux.find((x) => x.serie === serie && x.chapitre === numero && ["en_attente", "en_cours"].includes(x.etat));
  if (t) suivre(t.id, $("#suivi-chap"), () => { $("#suivi-chap").hidden = true; });
}

/* ---------- glossaire ---------- */
async function vueGlossaire(serie) {
  nav(serie, "glossaire");
  const g = await api("GET", `/api/series/${enc(serie)}/glossaire`);
  g.propositions = g.propositions || { noms: [], termes: {}, registres: [] };
  const noms = Object.entries(g.noms || {}), termes = Object.entries(g.termes || {});
  const registres = (g.registres || []).slice();

  vue.innerHTML = `
    <h1>Glossaire</h1>
    <p class="sous-titre">Ce qui est écrit ici est imposé au traducteur pour tous les chapitres de ${esc(serie)}. C'est le meilleur moyen de garder les noms et le tutoiement cohérents.</p>
    <form id="f-gloss">
      <div class="sections-glossaire">
        <section class="panneau"><h2>Noms propres</h2><p class="aide" style="margin:0 0 12px">Personnages, lieux, guildes : orthographe en anglais, puis en français.</p>
          <div class="paires" id="p-noms"></div><button class="btn btn-discret" type="button" data-ajout="noms">Ajouter un nom</button></section>
        <section class="panneau"><h2>Termes</h2><p class="aide" style="margin:0 0 12px">Compétences, rangs, objets récurrents.</p>
          <div class="paires" id="p-termes"></div><button class="btn btn-discret" type="button" data-ajout="termes">Ajouter un terme</button></section>
        <section class="panneau"><h2>Tutoiement et vouvoiement</h2><p class="aide" style="margin:0 0 12px">L'anglais ne fait pas la différence : fixe-la pour chaque paire de personnages.</p>
          <div class="paires" id="p-registres"></div><button class="btn btn-discret" type="button" data-ajout="registres">Ajouter une paire</button></section>
        <section class="panneau"><h2>Honorifiques</h2><p class="aide" style="margin:0 0 12px">hyung, noona, sunbae, -nim…</p>
          <div class="radios"><label class="case"><input type="radio" name="honorifiques" value="garder" ${g.honorifiques !== "adapter" ? "checked" : ""}> Les garder tels quels</label>
          <label class="case"><input type="radio" name="honorifiques" value="adapter" ${g.honorifiques === "adapter" ? "checked" : ""}> Les adapter en français</label></div></section>
        <section class="panneau" id="s-props"></section>
      </div>
      <div class="pied-formulaire"><button class="btn btn-plein">Enregistrer le glossaire</button></div>
    </form>`;

  const paire = (a, b, ph1, ph2) => `<div class="paire"><input type="text" value="${esc(a)}" placeholder="${ph1}" aria-label="${ph1}"><input type="text" value="${esc(b)}" placeholder="${ph2}" aria-label="${ph2}"><button class="suppr" type="button" aria-label="Supprimer">×</button></div>`;
  const reg = (r) => `<div class="paire trois"><input type="text" value="${esc(r.a)}" placeholder="Personnage" aria-label="Personnage"><input type="text" value="${esc(r.b)}" placeholder="Autre personnage" aria-label="Autre personnage">
    <select aria-label="Registre"><option value="tu" ${r.registre === "tu" ? "selected" : ""}>tu</option><option value="vous" ${r.registre === "vous" ? "selected" : ""}>vous</option></select><button class="suppr" type="button" aria-label="Supprimer">×</button></div>`;
  const remplir = () => {
    $("#p-noms").innerHTML = noms.map(([a, b]) => paire(a, b, "En anglais", "En français")).join("");
    $("#p-termes").innerHTML = termes.map(([a, b]) => paire(a, b, "En anglais", "En français")).join("");
    $("#p-registres").innerHTML = registres.map(reg).join("");
    $$(".suppr").forEach((s) => s.addEventListener("click", () => s.parentElement.remove()));
  };
  remplir();
  $$("[data-ajout]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.ajout, cont = $(`#p-${k}`);
    cont.insertAdjacentHTML("beforeend", k === "registres" ? reg({ a: "", b: "", registre: "tu" }) : paire("", "", "En anglais", "En français"));
    const der = cont.lastElementChild;
    $(".suppr", der).addEventListener("click", () => der.remove());
    $("input", der).focus();
  }));

  const lire = () => {
    const ent = (id) => $$(`#${id} .paire`).map((p) => $$("input", p).map((i) => i.value.trim())).filter(([a]) => a);
    return {
      ...g,
      noms: Object.fromEntries(ent("p-noms").map(([a, b]) => [a, b || a])),
      termes: Object.fromEntries(ent("p-termes").map(([a, b]) => [a, b || a])),
      registres: $$("#p-registres .paire").map((p) => { const [a, b] = $$("input", p).map((i) => i.value.trim()); return { a, b, registre: $("select", p).value }; }).filter((r) => r.a && r.b),
      honorifiques: $("input[name=honorifiques]:checked").value,
    };
  };

  const props = () => {
    const p = g.propositions;
    const items = [
      ...p.noms.map((n) => ({ t: "nom", lib: n, val: n })),
      ...Object.entries(p.termes || {}).map(([a, b]) => ({ t: "terme", lib: `${a} : ${b}`, val: [a, b] })),
      ...(p.registres || []).map((r) => ({ t: "registre", lib: `${r.a} et ${r.b} : ${r.registre === "vous" ? "vouvoiement" : "tutoiement"}`, val: r })),
    ];
    $("#s-props").innerHTML = `<h2>Proposé par le traducteur</h2>
      ${items.length ? `<p class="aide" style="margin:0 0 8px">Repéré pendant les traductions. Rien n'est appliqué tant que tu ne l'ajoutes pas.</p>
        ${items.map((it, i) => `<div class="proposition"><span>${esc(it.lib)}</span><span><button class="btn btn-discret" type="button" data-i="${i}" data-act="non">Ignorer</button>
          <button class="btn" type="button" data-i="${i}" data-act="oui">Ajouter</button></span></div>`).join("")}`
        : `<p class="aide" style="margin:0">Rien à proposer pour l'instant. Les noms et les paires tu/vous repérés pendant les traductions apparaîtront ici.</p>`}`;
    $$("#s-props [data-act]").forEach((b) => b.addEventListener("click", () => {
      const it = items[+b.dataset.i];
      if (b.dataset.act === "oui") {
        if (it.t === "nom") $("#p-noms").insertAdjacentHTML("beforeend", paire(it.val, it.val, "En anglais", "En français"));
        if (it.t === "terme") $("#p-termes").insertAdjacentHTML("beforeend", paire(it.val[0], it.val[1], "En anglais", "En français"));
        if (it.t === "registre") $("#p-registres").insertAdjacentHTML("beforeend", reg(it.val));
        $$(".suppr").forEach((s) => { s.onclick = () => s.parentElement.remove(); });
      }
      if (it.t === "nom") p.noms = p.noms.filter((n) => n !== it.val);
      if (it.t === "terme") delete p.termes[it.val[0]];
      if (it.t === "registre") p.registres = p.registres.filter((r) => r !== it.val);
      props();
      toast(b.dataset.act === "oui" ? "Ajouté. Pense à enregistrer le glossaire." : "Proposition ignorée.");
    }));
  };
  props();

  $("#f-gloss").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("PUT", `/api/series/${enc(serie)}/glossaire`, lire()); toast("Glossaire enregistré."); }
    catch (er) { toast(er.message, true); }
  });
}

/* ---------- contexte ---------- */
async function vueContexte(serie) {
  nav(serie, "contexte");
  const c = await api("GET", `/api/series/${enc(serie)}/contexte`);
  vue.innerHTML = `<h1>Contexte de la série</h1>
    <p class="sous-titre">Synopsis, personnages et un résumé par chapitre. Le traducteur lit les résumés des chapitres qui précèdent celui qu'il traduit, même si tu les traites dans le désordre. Un résumé est ajouté automatiquement après chaque traduction ; garde le titre « ## Chapitre N » pour chaque chapitre.</p>
    <form id="f-ctx" class="panneau">
      <textarea class="contexte" name="texte" aria-label="Contexte de la série" placeholder="# Contexte de la série&#10;&#10;## Synopsis&#10;…&#10;&#10;## Chapitre 1&#10;…">${esc(c.texte)}</textarea>
      <div class="pied-formulaire"><button class="btn btn-plein">Enregistrer le contexte</button></div>
    </form>`;
  $("#f-ctx").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("PUT", `/api/series/${enc(serie)}/contexte`, { texte: e.target.texte.value }); toast("Contexte enregistré."); }
    catch (er) { toast(er.message, true); }
  });
}

/* ---------- réglages ---------- */
async function vueReglages() {
  nav(null);
  const r = await api("GET", "/api/reglages");
  vue.innerHTML = `<h1>Réglages</h1>
    <p class="sous-titre">Le modèle de langue qui traduit. Il doit pouvoir lire les images pour savoir qui parle dans chaque scène.</p>
    <form id="f-reg" class="panneau" style="max-width:560px">
      <label class="champ"><span>Fournisseur</span><select name="fournisseur">
        <option value="anthropic" ${r.fournisseur === "anthropic" ? "selected" : ""}>Anthropic (Claude)</option>
        <option value="openai" ${r.fournisseur === "openai" ? "selected" : ""}>Compatible OpenAI (OpenAI, LM Studio, Ollama…)</option></select></label>
      <label class="champ"><span>Modèle</span><input type="text" name="modele" value="${esc(r.modele)}" autocomplete="off"></label>
      <label class="champ" id="z-url"><span>Adresse de l'API</span><input type="url" name="url_base" value="${esc(r.url_base)}" placeholder="https://api.openai.com/v1">
        <p class="aide">Pour un modèle local, par exemple http://localhost:1234/v1 avec LM Studio.</p></label>
      <label class="champ"><span>Clé API</span><input type="password" name="cle_api" autocomplete="off" placeholder="${r.cle_api_definie ? `Clé enregistrée${r.cle_api_fin ? ` (se termine par ${esc(r.cle_api_fin)})` : ""}. Laisse vide pour la garder.` : "Colle ta clé API"}">
        <p class="aide">Enregistrée seulement sur cet ordinateur, dans reglages.json.</p></label>
      <label class="case" style="margin-bottom:8px"><input type="checkbox" name="multimodal" ${r.multimodal ? "checked" : ""}> Envoyer les images des scènes au modèle</label>
      <p class="aide" style="margin:0 0 4px">Décoche seulement si ton modèle ne lit pas les images : la traduction perd alors l'information de qui parle.</p>
      <div class="pied-formulaire"><button class="btn btn-plein">Enregistrer les réglages</button></div>
    </form>`;
  const f = $("#f-reg");
  const majUrl = () => { $("#z-url").hidden = f.fournisseur.value !== "openai"; };
  f.fournisseur.addEventListener("change", majUrl); majUrl();
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("PUT", "/api/reglages", { fournisseur: f.fournisseur.value, modele: f.modele.value.trim(), url_base: f.url_base.value.trim(),
        cle_api: f.cle_api.value.trim() || null, multimodal: f.multimodal.checked });
      toast("Réglages enregistrés."); vueReglages();
    } catch (er) { toast(er.message, true); }
  });
}

router();
