const UI = {
  fr: {
    lang: "Langue",
    search: "Rechercher",
    open: "Charger un fichier",
    sources: "Sources",
    children: "Branches",
    terms: "termes",
    treeTitle: "Arbre complet",
    underAsk: "Développée dans le cadre d’askUZH",
    brandTag: "Taxonomie du web UZH",
    empty: "Aucun terme ne correspond.",
    loadError: "taxonomy.json n'a pas été chargé. Depuis la racine du dépôt, ouvrez cette page avec un serveur local, ou chargez le fichier ici.",
    invalid: "Ce fichier n'est pas une taxonomie lisible.",
    expand: "Déplier",
    collapse: "Replier",
    langs: { de: "Allemand", fr: "Français", en: "Anglais" }
  },
  de: {
    lang: "Sprache",
    search: "Suchen",
    open: "Datei laden",
    sources: "Quellen",
    children: "Zweige",
    terms: "Begriffe",
    treeTitle: "Gesamter Baum",
    underAsk: "Entwickelt im Rahmen von askUZH",
    brandTag: "Taxonomie des UZH-Webs",
    empty: "Kein Begriff passt.",
    loadError: "taxonomy.json wurde nicht geladen. Öffnen Sie die Seite von der Projektwurzel über einen lokalen Server, oder laden Sie die Datei hier.",
    invalid: "Diese Datei ist keine lesbare Taxonomie.",
    expand: "Aufklappen",
    collapse: "Zuklappen",
    langs: { de: "Deutsch", fr: "Französisch", en: "Englisch" }
  },
  en: {
    lang: "Language",
    search: "Search",
    open: "Load a file",
    sources: "Sources",
    children: "Branches",
    terms: "terms",
    treeTitle: "Full tree",
    underAsk: "Developed within askUZH",
    brandTag: "UZH web taxonomy",
    empty: "No term matches.",
    loadError: "taxonomy.json did not load. From the repository root, open this page through a local server, or load the file here.",
    invalid: "This file is not a readable taxonomy.",
    expand: "Expand",
    collapse: "Collapse",
    langs: { de: "German", fr: "French", en: "English" }
  }
};

const ORDER = ["de", "fr", "en"];
const state = {
  data: null,
  lang: "fr",
  selected: null,
  activeAxis: null,
  query: "",
  open: new Set(),
  openAxes: new Set()
};

const titleEl = document.getElementById("title");
const brandTagEl = document.getElementById("brand-tag");
const underAskEl = document.getElementById("under-ask");
const langsEl = document.getElementById("langs");
const langLabel = document.getElementById("lang-label");
const searchLabel = document.getElementById("search-label");
const fileLabel = document.getElementById("file-label");
const queryEl = document.getElementById("q");
const provenanceEl = document.getElementById("provenance");
const errorEl = document.getElementById("error");
const chaptersEl = document.getElementById("chapters");
const appEl = document.getElementById("app");
const treeEl = document.getElementById("tree");
const ficheEl = document.getElementById("fiche");
const railTitleEl = document.getElementById("rail-title");
const railCountEl = document.getElementById("rail-count");

document.getElementById("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
});

document.getElementById("home-link").addEventListener("click", (event) => {
  event.preventDefault();
  if (!state.data) return;
  focusAxis(state.data.axes[0].id, true);
});

queryEl.addEventListener("input", () => {
  state.query = queryEl.value.trim().toLowerCase();
  if (state.query && state.data) {
    const hit = firstMatch();
    if (hit) {
      state.activeAxis = hit.axis.id;
      state.openAxes.add(hit.axis.id);
      state.selected = hit.node ? hit.node.id : hit.axis.id;
      reveal(state.selected);
    }
  }
  render();
});

document.getElementById("file").addEventListener("change", async (event) => {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  try {
    boot(JSON.parse(await file.text()));
  } catch {
    showError(UI[state.lang].invalid);
  }
});

window.addEventListener("hashchange", () => {
  const id = location.hash.replace(/^#/, "");
  if (id && find(id)) select(id, false);
});

function text(bag) {
  if (!bag) return "";
  return bag[state.lang] || bag.fr || bag.de || bag.en || "";
}

function blob(item) {
  return ORDER.flatMap((code) => [item.labels?.[code], item.definition?.[code]])
    .filter(Boolean)
    .join("\n")
    .toLowerCase();
}

function childrenOf(axis, parent) {
  return axis.nodes.filter((node) => node.parent === parent);
}

function find(id) {
  if (!state.data) return null;
  for (const axis of state.data.axes) {
    if (axis.id === id) return { kind: "axis", axis, node: null };
    const node = axis.nodes.find((item) => item.id === id);
    if (node) return { kind: "node", axis, node };
  }
  return null;
}

function matches(item) {
  if (!state.query) return true;
  return blob(item).includes(state.query);
}

function hasMatch(axis, node) {
  if (matches(node)) return true;
  return childrenOf(axis, node.id).some((child) => hasMatch(axis, child));
}

function axisVisible(axis) {
  if (!state.query) return true;
  if (matches(axis)) return true;
  return childrenOf(axis, null).some((node) => hasMatch(axis, node));
}

function firstMatch() {
  for (const axis of state.data.axes) {
    if (matches(axis)) return { axis, node: null };
    for (const node of axis.nodes) {
      if (matches(node)) return { axis, node };
    }
  }
  return null;
}

function showError(message) {
  errorEl.hidden = false;
  errorEl.textContent = message;
  appEl.hidden = true;
  chaptersEl.hidden = true;
}

function boot(data) {
  if (!data || !Array.isArray(data.axes)) {
    showError(UI[state.lang].invalid);
    return;
  }
  state.data = data;
  state.open = new Set();
  state.openAxes = new Set();
  errorEl.hidden = true;
  appEl.hidden = false;
  chaptersEl.hidden = false;
  const hashed = location.hash.replace(/^#/, "");
  const initial = (hashed && find(hashed)) || { kind: "axis", axis: data.axes[0], node: null };
  state.selected = initial.kind === "axis" ? initial.axis.id : initial.node.id;
  state.activeAxis = initial.axis.id;
  state.openAxes.add(initial.axis.id);
  reveal(state.selected);
  render();
}

function reveal(id) {
  const found = find(id);
  if (!found) return;
  state.openAxes.add(found.axis.id);
  if (!found.node) return;
  let parent = found.node.parent;
  while (parent) {
    state.open.add(parent);
    parent = found.axis.nodes.find((item) => item.id === parent)?.parent || null;
  }
}

function focusAxis(axisId, writeHash) {
  state.activeAxis = axisId;
  state.selected = axisId;
  state.openAxes.add(axisId);
  if (writeHash) history.replaceState(null, "", `#${axisId}`);
  render();
}

function select(id, writeHash) {
  const found = find(id);
  if (!found) return;
  state.selected = id;
  state.activeAxis = found.axis.id;
  reveal(id);
  if (writeHash) history.replaceState(null, "", `#${id}`);
  render();
  if (window.matchMedia("(max-width: 720px)").matches) {
    ficheEl.scrollIntoView({ block: "start" });
  }
}

function blurb(axis) {
  const raw = text(axis.definition);
  const cut = raw.split(/(?<=[.!?])\s+/)[0] || raw;
  return cut.length > 140 ? `${cut.slice(0, 137)}…` : cut;
}

function renderLangs() {
  const ui = UI[state.lang];
  langLabel.textContent = ui.lang;
  searchLabel.textContent = ui.search;
  fileLabel.textContent = ui.open;
  queryEl.setAttribute("aria-label", ui.search);
  queryEl.placeholder = ui.search;
  brandTagEl.textContent = ui.brandTag;
  underAskEl.textContent = ui.underAsk;
  langsEl.replaceChildren();
  langsEl.setAttribute("aria-label", ui.lang);
  for (const code of ORDER) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = code;
    button.setAttribute("aria-pressed", String(code === state.lang));
    button.setAttribute("aria-label", ui.langs[code]);
    button.addEventListener("click", () => {
      state.lang = code;
      document.documentElement.lang = code;
      render();
    });
    langsEl.appendChild(button);
  }
}

function renderChapters() {
  chaptersEl.replaceChildren();
  state.data.axes.forEach((axis, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chapter";
    button.dataset.axis = axis.id;
    if (state.activeAxis === axis.id) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => focusAxis(axis.id, true));

    const kicker = document.createElement("div");
    kicker.className = "chapter-kicker";
    const label = document.createElement("span");
    label.textContent = String(index + 1).padStart(2, "0");
    const count = document.createElement("span");
    count.className = "chapter-count";
    count.textContent = `${axis.nodes.length} ${UI[state.lang].terms}`;
    kicker.append(label, count);

    const name = document.createElement("p");
    name.className = "chapter-name";
    name.textContent = text(axis.labels);

    const note = document.createElement("p");
    note.className = "chapter-blurb";
    note.textContent = blurb(axis);

    button.append(kicker, name, note);
    chaptersEl.appendChild(button);
  });
}

function renderTree() {
  const ui = UI[state.lang];
  const total = state.data.axes.reduce((sum, axis) => sum + axis.nodes.length, 0);
  railTitleEl.textContent = ui.treeTitle;
  railCountEl.textContent = `${total} ${ui.terms}`;
  treeEl.replaceChildren();

  const forest = document.createElement("ul");
  forest.className = "forest";
  let any = false;

  for (const axis of state.data.axes) {
    if (!axisVisible(axis)) continue;
    any = true;
    const open = state.query ? true : state.openAxes.has(axis.id);
    const block = document.createElement("li");
    block.className = "axis-block";
    block.dataset.axis = axis.id;
    block.dataset.open = String(open);
    block.dataset.active = String(state.activeAxis === axis.id);

    const row = document.createElement("div");
    row.className = "axis-row";

    const twist = document.createElement("button");
    twist.type = "button";
    twist.className = "axis-twist";
    twist.textContent = open ? "–" : "+";
    twist.setAttribute("aria-expanded", String(open));
    twist.setAttribute("aria-label", open ? ui.collapse : ui.expand);
    twist.addEventListener("click", () => {
      if (state.query) return;
      if (open) {
        state.openAxes.delete(axis.id);
        render();
        return;
      }
      state.openAxes.add(axis.id);
      state.activeAxis = axis.id;
      state.selected = axis.id;
      history.replaceState(null, "", `#${axis.id}`);
      render();
    });

    const head = document.createElement("button");
    head.type = "button";
    head.className = "axis-head";
    if (state.selected === axis.id) head.setAttribute("aria-current", "true");
    const headLabel = document.createElement("span");
    headLabel.textContent = text(axis.labels);
    const headMeta = document.createElement("span");
    headMeta.className = "axis-meta";
    headMeta.textContent = String(axis.nodes.length);
    head.append(headLabel, headMeta);
    head.addEventListener("click", () => {
      state.openAxes.add(axis.id);
      focusAxis(axis.id, true);
    });

    row.append(twist, head);
    block.appendChild(row);

    if (open) {
      const body = document.createElement("div");
      body.className = "axis-body";
      const list = document.createElement("ul");
      list.className = "branch";
      fill(list, axis, null, Boolean(state.query) && matches(axis));
      body.appendChild(list);
      block.appendChild(body);
    }

    forest.appendChild(block);
  }

  if (!any) {
    const note = document.createElement("p");
    note.className = "empty";
    note.textContent = ui.empty;
    treeEl.appendChild(note);
    return;
  }

  treeEl.appendChild(forest);
}

function fill(list, axis, parent, inherited) {
  for (const node of childrenOf(axis, parent)) {
    const self = matches(node);
    if (state.query && !inherited && !hasMatch(axis, node)) continue;
    const kids = childrenOf(axis, node.id);
    const open = state.query ? inherited || self || hasMatch(axis, node) : state.open.has(node.id);
    const item = document.createElement("li");
    item.className = "node";

    const row = document.createElement("div");
    row.className = "node-row";

    const twist = document.createElement("button");
    twist.type = "button";
    twist.className = "node-twist";
    if (kids.length === 0) {
      twist.disabled = true;
      twist.tabIndex = -1;
      twist.textContent = "";
    } else {
      twist.textContent = open ? "–" : "+";
      twist.setAttribute("aria-expanded", String(open));
      twist.setAttribute("aria-label", open ? UI[state.lang].collapse : UI[state.lang].expand);
      twist.addEventListener("click", () => {
        if (state.open.has(node.id)) state.open.delete(node.id);
        else {
          state.open.add(node.id);
          state.openAxes.add(axis.id);
          state.activeAxis = axis.id;
        }
        render();
      });
    }

    const term = document.createElement("button");
    term.type = "button";
    term.className = "node-btn";
    const label = document.createElement("span");
    label.textContent = text(node.labels);
    term.appendChild(label);
    if (kids.length) {
      const meta = document.createElement("span");
      meta.className = "node-meta";
      meta.textContent = String(kids.length);
      term.appendChild(meta);
    }
    if (state.selected === node.id) term.setAttribute("aria-current", "true");
    term.addEventListener("click", () => {
      if (kids.length) state.open.add(node.id);
      select(node.id, true);
    });

    row.append(twist, term);
    item.appendChild(row);

    if (kids.length && open) {
      const nested = document.createElement("ul");
      nested.className = "leaves";
      fill(nested, axis, node.id, inherited || self);
      item.appendChild(nested);
    }

    list.appendChild(item);
  }
}

function renderFiche() {
  const found = find(state.selected) || { kind: "axis", axis: state.data.axes[0], node: null };
  const item = found.node || found.axis;
  const ui = UI[state.lang];
  ficheEl.replaceChildren();
  ficheEl.style.setProperty("--chapter-color", chapterColor(found.axis.id));

  const crumbs = document.createElement("p");
  crumbs.className = "crumbs";
  const trail = [found.axis];
  if (found.node) {
    const chain = [];
    let parent = found.node.parent;
    while (parent) {
      const ancestor = found.axis.nodes.find((node) => node.id === parent);
      if (!ancestor) break;
      chain.unshift(ancestor);
      parent = ancestor.parent;
    }
    trail.push(...chain);
  }
  trail.forEach((entry, index) => {
    if (index) {
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "/";
      crumbs.appendChild(sep);
    }
    if (entry.id === item.id) {
      crumbs.append(text(entry.labels));
      return;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text(entry.labels);
    button.addEventListener("click", () => select(entry.id, true));
    crumbs.appendChild(button);
  });
  ficheEl.appendChild(crumbs);

  const badge = document.createElement("p");
  badge.className = "axis-badge";
  badge.textContent = text(found.axis.labels);
  ficheEl.appendChild(badge);

  const heading = document.createElement("h2");
  heading.textContent = text(item.labels);
  ficheEl.appendChild(heading);

  const others = document.createElement("ul");
  others.className = "others";
  for (const code of ORDER) {
    if (code === state.lang) continue;
    const line = document.createElement("li");
    const codeEl = document.createElement("span");
    codeEl.textContent = code;
    line.append(codeEl, document.createTextNode(item.labels?.[code] || ""));
    others.appendChild(line);
  }
  ficheEl.appendChild(others);

  const definition = document.createElement("p");
  definition.className = "definition";
  definition.textContent = text(item.definition);
  ficheEl.appendChild(definition);

  if (found.node && state.data.status_legend?.[found.node.status]) {
    const status = document.createElement("p");
    status.className = "status";
    status.textContent = text(state.data.status_legend[found.node.status]);
    ficheEl.appendChild(status);
  }

  const kids = found.node
    ? childrenOf(found.axis, found.node.id)
    : childrenOf(found.axis, null);
  if (kids.length) {
    const h = document.createElement("h3");
    h.textContent = ui.children;
    const list = document.createElement("ul");
    list.className = "children";
    for (const child of kids) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "child";
      button.textContent = text(child.labels);
      button.addEventListener("click", () => {
        state.open.add(child.id);
        select(child.id, true);
      });
      li.appendChild(button);
      list.appendChild(li);
    }
    ficheEl.append(h, list);
  }

  const sources = found.node?.sources || [];
  if (sources.length) {
    const h = document.createElement("h3");
    h.textContent = ui.sources;
    const list = document.createElement("ul");
    list.className = "sources";
    for (const url of sources) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = url;
      a.textContent = url;
      a.target = "_blank";
      a.rel = "noreferrer";
      li.appendChild(a);
      list.appendChild(li);
    }
    ficheEl.append(h, list);
  }
}

function chapterColor(axisId) {
  return ({
    organisation: "#0028a5",
    publics: "#0a5c8a",
    types_document: "#1f4d3a",
    offre_etudes: "#1b4f72"
  })[axisId] || "#0028a5";
}

function render() {
  if (!state.data) return;
  const ui = UI[state.lang];
  titleEl.textContent = text(state.data.title);
  document.title = `${text(state.data.title)} · askUZH`;
  provenanceEl.textContent = text(state.data.provenance);
  renderLangs();
  renderChapters();
  renderTree();
  renderFiche();
  if (!errorEl.hidden) errorEl.textContent = ui.loadError;
}

fetch("../taxonomy.json")
  .then((response) => {
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  })
  .then(boot)
  .catch(() => showError(UI[state.lang].loadError));
