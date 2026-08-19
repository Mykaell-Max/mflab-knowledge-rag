"use strict";

const state = {
  key: sessionStorage.getItem("mflab-api-key") || "",
  repositories: [],
};

const views = {
  overview: "Visão geral",
  search: "Buscar",
  ask: "Perguntar",
};

const byId = (id) => document.getElementById(id);
const formatNumber = (value) => new Intl.NumberFormat("pt-BR").format(Number(value || 0));
const formatPercent = (value) => `${(Number(value || 0) * 100).toFixed(1).replace(".0", "")}%`;
const statusLabels = {
  success: "Concluída",
  warning: "Concluída com avisos",
  failed: "Falhou",
  running: "Em execução",
};

function formatStatus(value) {
  return statusLabels[value] || value || "Não disponível";
}

function setComponentState(id, text, healthy = true) {
  const target = byId(id);
  target.textContent = text;
  const dot = target.closest("div")?.querySelector(".service-dot");
  if (dot) dot.classList.toggle("ok", healthy);
}

function apiHeaders(hasBody = false) {
  const headers = {};
  if (hasBody) headers["Content-Type"] = "application/json";
  if (state.key) headers.Authorization = `Bearer ${state.key}`;
  return headers;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...apiHeaders(Boolean(options.body)), ...(options.headers || {}) },
  });
  if (response.status === 401) {
    showAuth();
    throw new Error("A chave de acesso é necessária ou não foi aceita.");
  }
  if (!response.ok) {
    let message = `Falha HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body.detail) message = body.detail;
    } catch (_) { /* resposta sem JSON */ }
    throw new Error(message);
  }
  return response.json();
}

function element(name, className, text) {
  const node = document.createElement(name);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showToast(message) {
  const toast = byId("global-feedback");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.setTimeout(() => toast.classList.add("hidden"), 4500);
}

function showAuth(message = "") {
  byId("auth-error").textContent = message;
  byId("auth-overlay").classList.remove("hidden");
  window.setTimeout(() => byId("api-key").focus(), 0);
}

function hideAuth() {
  byId("auth-overlay").classList.add("hidden");
  byId("auth-error").textContent = "";
  byId("api-key").value = "";
}

function switchView(name) {
  if (!views[name]) return;
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
  byId(`view-${name}`).classList.add("active");
  document.querySelector(`[data-view="${name}"]`).classList.add("active");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function repositoryRow(repository) {
  const row = element("article", "repository-row");
  const identity = element("div");
  identity.append(element("strong", "", repository.project));
  identity.append(element("small", "", repository.repository_id));
  row.append(identity);

  const metrics = [
    ["Branches", repository.branches],
    ["Chunks", formatNumber(repository.chunks)],
    ["Vetores", formatPercent(repository.embedding_coverage)],
  ];
  metrics.forEach(([label, value]) => {
    const cell = element("div", "repository-stat");
    cell.append(element("strong", "", String(value)));
    cell.append(element("small", "", label));
    row.append(cell);
  });
  return row;
}

function renderRepositories(repositories) {
  const list = byId("repository-list");
  list.classList.remove("loading-block");
  list.replaceChildren();
  repositories.forEach((repository) => list.append(repositoryRow(repository)));
  if (!repositories.length) list.append(element("p", "", "Nenhum repositório visível."));

  const projects = [...new Set(repositories.map((repository) => repository.project))].sort();
  ["search-project", "ask-project"].forEach((id) => {
    const select = byId(id);
    const current = select.value;
    select.replaceChildren(new Option("Todos", ""));
    projects.forEach((project) => select.add(new Option(project, project)));
    select.value = projects.includes(current) ? current : "";
  });
}

function updateBranches(projectSelectId, branchSelectId) {
  const project = byId(projectSelectId).value;
  const select = byId(branchSelectId);
  select.replaceChildren(new Option("Todas", ""));
  if (!project) return;
  const repository = state.repositories.find((item) => item.project === project);
  (repository?.canonical_branches || []).forEach((branch) => select.add(new Option(branch, branch)));
}

function renderRun(indexer) {
  const target = byId("run-summary");
  target.classList.remove("loading-block");
  target.replaceChildren();
  if (!indexer) {
    target.append(element("p", "", "Nenhuma execução registrada."));
    return;
  }
  const status = element("div", "run-status");
  status.append(element("span", "pulse"));
  status.append(element("strong", "", formatStatus(indexer.status)));
  target.append(status);

  const details = element("div", "run-detail");
  const entries = [
    ["Run ID", indexer.run_id || "—"],
    ["Etapa", indexer.stage || indexer.last_event?.message || "Concluída"],
    ["Duração", indexer.duration_seconds == null ? "—" : `${indexer.duration_seconds}s`],
    ["Atualizado", indexer.updated_at ? new Date(indexer.updated_at).toLocaleString("pt-BR") : "—"],
  ];
  entries.forEach(([label, value]) => {
    const row = element("div");
    row.append(element("span", "", label));
    row.append(element("span", "", String(value)));
    details.append(row);
  });
  target.append(details);
  if (indexer.progress?.percent != null) {
    const track = element("div", "progress-track");
    const bar = element("div", "progress-bar");
    bar.style.width = `${Math.max(0, Math.min(100, indexer.progress.percent))}%`;
    track.append(bar);
    target.append(track);
  }
}

async function refreshOverview() {
  const [health, status, catalog] = await Promise.all([
    api("/health"),
    api("/status"),
    api("/repositories"),
  ]);
  state.repositories = catalog.repositories || [];
  byId("metric-repositories").textContent = formatNumber(health.repositories);
  byId("metric-chunks").textContent = formatNumber(health.chunks);
  const chunks = state.repositories.reduce((sum, item) => sum + Number(item.chunks || 0), 0);
  const embedded = state.repositories.reduce((sum, item) => sum + Number(item.embedded_chunks || 0), 0);
  byId("metric-coverage").textContent = chunks ? formatPercent(embedded / chunks) : "—";
  byId("metric-indexer").textContent = formatStatus(status.indexer?.status);
  byId("metric-indexer-detail").textContent = status.indexer?.run_id || "sem execução registrada";
  setComponentState("database-state", health.database === "ok" ? "Conectado" : "Indisponível", health.database === "ok");
  setComponentState("embedding-state", status.search?.model_loaded ? "Modelo carregado" : "Carregamento sob demanda");
  setComponentState("generation-state", status.generation?.configured ? "Configurado" : "Não configurado", Boolean(status.generation?.configured));
  setComponentState("authentication-state", status.authentication?.configured ? "Chave de rede ativa" : "Somente local");
  byId("health-badge").textContent = "Operacional";
  byId("health-badge").className = "status-badge ok";
  renderRepositories(state.repositories);
  renderRun(status.indexer);
}

function resultCard(result, sourceId = "") {
  const card = element("article", "result-card");
  const meta = element("div", "result-meta");
  const occurrence = result.selected_occurrence || {};
  [sourceId, result.project, occurrence.branch, String(occurrence.commit_sha || "").slice(0, 12)]
    .filter(Boolean)
    .forEach((value, index) => meta.append(element("span", `chip ${index === 0 && sourceId ? "accent" : ""}`, value)));
  card.append(meta);
  card.append(element("h3", "", result.path || "Fonte sem caminho"));
  if (result.text) card.append(element("p", "", result.text));
  card.append(element("p", "citation", result.citation || ""));
  return card;
}

async function submitSearch(event) {
  event.preventDefault();
  const feedback = byId("search-feedback");
  const results = byId("search-results");
  feedback.textContent = "Buscando evidências…";
  results.replaceChildren();
  const payload = {
    query: byId("search-query").value.trim(),
    mode: byId("search-mode").value,
    limit: 10,
  };
  if (byId("search-project").value) payload.project = byId("search-project").value;
  if (byId("search-branch").value) payload.branch = byId("search-branch").value;
  try {
    const response = await api("/search", { method: "POST", body: JSON.stringify(payload) });
    feedback.textContent = `${response.count} trechos encontrados.`;
    response.results.forEach((result) => results.append(resultCard(result)));
  } catch (error) {
    feedback.textContent = error.message;
  }
}

async function submitAsk(event) {
  event.preventDefault();
  const feedback = byId("ask-feedback");
  const card = byId("answer-card");
  const sources = byId("answer-sources");
  feedback.textContent = "Recuperando evidências e elaborando a resposta…";
  card.classList.add("hidden");
  card.replaceChildren();
  sources.replaceChildren();
  const payload = {
    query: byId("ask-query").value.trim(),
    mode: "hybrid",
    limit: 10,
    max_context_characters: 16000,
    max_output_tokens: 900,
    temperature: 0,
  };
  if (byId("ask-project").value) payload.project = byId("ask-project").value;
  if (byId("ask-branch").value) payload.branch = byId("ask-branch").value;
  try {
    const response = await api("/ask", { method: "POST", body: JSON.stringify(payload) });
    feedback.textContent = response.abstained ? "Não há evidência indexada suficiente." : "Resposta concluída.";
    card.append(element("h3", "", response.abstained ? "Evidência insuficiente" : "Resposta"));
    card.append(element("div", "answer-text", response.answer || "A base indexada não sustenta uma resposta."));
    const footer = element("div", "answer-footer");
    footer.append(element("span", "chip accent", response.grounding_status || "sem status"));
    footer.append(element("span", "chip", `${(response.citations_used || []).length} citações`));
    footer.append(element("span", "chip", `${response.duration_seconds || 0}s`));
    card.append(footer);
    card.classList.remove("hidden");
    (response.sources || []).forEach((source) => sources.append(resultCard(source, source.source_id)));
  } catch (error) {
    feedback.textContent = error.message;
  }
}

async function bootstrap() {
  try {
    await refreshOverview();
    hideAuth();
  } catch (error) {
    byId("health-badge").textContent = "Acesso necessário";
    byId("health-badge").className = "status-badge pending";
    if (String(error.message).includes("chave")) showAuth(error.message);
    else showToast(error.message);
  }
}

document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
byId("refresh-button").addEventListener("click", () => refreshOverview().catch((error) => showToast(error.message)));
byId("search-project").addEventListener("change", () => updateBranches("search-project", "search-branch"));
byId("ask-project").addEventListener("change", () => updateBranches("ask-project", "ask-branch"));
byId("search-form").addEventListener("submit", submitSearch);
byId("ask-form").addEventListener("submit", submitAsk);
byId("auth-button").addEventListener("click", () => showAuth());
byId("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.key = byId("api-key").value.trim();
  try {
    await api("/status");
    sessionStorage.setItem("mflab-api-key", state.key);
    hideAuth();
    await refreshOverview();
  } catch (error) {
    state.key = "";
    sessionStorage.removeItem("mflab-api-key");
    showAuth(error.message);
  }
});

bootstrap();
