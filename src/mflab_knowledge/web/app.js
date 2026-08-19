"use strict";

const state = {
  repositories: [],
  adminAuthenticated: false,
};

const views = new Set(["ask", "search", "admin"]);
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

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "Não disponível";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let selected = bytes;
  let index = 0;
  while (selected >= 1024 && index < units.length - 1) {
    selected /= 1024;
    index += 1;
  }
  const digits = index > 2 ? 1 : 0;
  return `${selected.toFixed(digits)} ${units[index]}`;
}

function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds || 0));
  if (value < 60) return `${Math.round(value)} s`;
  if (value < 3600) return `${Math.floor(value / 60)} min`;
  if (value < 86400) return `${Math.floor(value / 3600)} h ${Math.floor((value % 3600) / 60)} min`;
  return `${Math.floor(value / 86400)} d ${Math.floor((value % 86400) / 3600)} h`;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers,
  });
  if (!response.ok) {
    let message = `Falha HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body.detail) message = body.detail;
    } catch (_) { /* resposta sem JSON */ }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function element(name, className, text) {
  const node = document.createElement(name);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function sourceLabel(source, sourceId) {
  if (!source) return sourceId;
  if (source.source_kind === "derived_structure") {
    return `${source.project || "Estrutura"} · mapa indexado`;
  }
  const path = String(source.path || "Fonte");
  const filename = path.split("/").filter(Boolean).pop() || path;
  const start = Number(source.line_start);
  const end = Number(source.line_end);
  if (!Number.isFinite(start)) return filename;
  return `${filename} · L${start}${Number.isFinite(end) && end !== start ? `–${end}` : ""}`;
}

function citationReference(source, sourceId) {
  if (!source) {
    const missing = element("span", "inline-citation invalid", sourceId);
    missing.title = `A resposta citou ${sourceId}, mas essa fonte não foi devolvida.`;
    return missing;
  }
  const occurrence = source.selected_occurrence || {};
  const reference = element("a", "inline-citation", sourceLabel(source, sourceId));
  reference.href = `#source-${sourceId}`;
  reference.dataset.sourceId = sourceId;
  reference.title = [
    sourceId,
    source.project,
    occurrence.branch,
    String(occurrence.commit_sha || "").slice(0, 12),
    source.path,
  ].filter(Boolean).join(" · ");
  reference.setAttribute("aria-label", `Ver fonte ${sourceId}: ${sourceLabel(source, sourceId)}`);
  return reference;
}

function appendInlineMarkdown(target, text, sourceIndex) {
  const tokenPattern = /(\[(?:S\d+(?:\s*[,;]\s*S\d+)*)\]|`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g;
  let position = 0;
  for (const match of text.matchAll(tokenPattern)) {
    if (match.index > position) target.append(document.createTextNode(text.slice(position, match.index)));
    const token = match[0];
    if (token.startsWith("[S")) {
      const group = element("span", "citation-group");
      (token.match(/S\d+/g) || []).forEach((sourceId) => {
        group.append(citationReference(sourceIndex.get(sourceId), sourceId));
      });
      target.append(group);
    } else if (token.startsWith("`")) {
      target.append(element("code", "inline-code", token.slice(1, -1)));
    } else if (token.startsWith("**")) {
      const strong = element("strong");
      appendInlineMarkdown(strong, token.slice(2, -2), sourceIndex);
      target.append(strong);
    } else {
      const emphasis = element("em");
      appendInlineMarkdown(emphasis, token.slice(1, -1), sourceIndex);
      target.append(emphasis);
    }
    position = match.index + token.length;
  }
  if (position < text.length) target.append(document.createTextNode(text.slice(position)));
}

function isMarkdownBlockStart(line) {
  return /^\s*$/.test(line)
    || /^\s{0,3}```/.test(line)
    || /^\s{0,3}#{1,4}\s+/.test(line)
    || /^\s{0,3}>\s?/.test(line)
    || /^\s{0,3}[-+*]\s+/.test(line)
    || /^\s{0,3}\d+[.)]\s+/.test(line)
    || /^\s{0,3}(?:---+|___+|\*\*\*+)\s*$/.test(line);
}

function renderMarkdown(markdown, sources) {
  const container = element("div", "answer-text markdown-body");
  const sourceIndex = new Map(
    (sources || []).map((source) => [String(source.source_id || ""), source]),
  );
  const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^\s{0,3}```\s*([A-Za-z0-9_+.-]{0,32})\s*$/);
    if (fence) {
      const language = fence[1] || "text";
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^\s{0,3}```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const block = element("div", "code-block");
      block.append(element("div", "code-language", language));
      const pre = element("pre");
      const code = element("code", `language-${language}`, codeLines.join("\n"));
      pre.append(code);
      block.append(pre);
      container.append(block);
      continue;
    }

    const heading = line.match(/^\s{0,3}(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = Math.min(6, heading[1].length + 2);
      const title = element(`h${level}`);
      appendInlineMarkdown(title, heading[2].trim(), sourceIndex);
      container.append(title);
      index += 1;
      continue;
    }

    if (/^\s{0,3}>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^\s{0,3}>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s{0,3}>\s?/, ""));
        index += 1;
      }
      const quote = element("blockquote");
      appendInlineMarkdown(quote, quoteLines.join(" "), sourceIndex);
      container.append(quote);
      continue;
    }

    const unordered = line.match(/^\s{0,3}[-+*]\s+(.+)$/);
    const ordered = line.match(/^\s{0,3}\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const list = element(ordered ? "ol" : "ul");
      const pattern = ordered ? /^\s{0,3}\d+[.)]\s+(.+)$/ : /^\s{0,3}[-+*]\s+(.+)$/;
      while (index < lines.length) {
        const item = lines[index].match(pattern);
        if (!item) break;
        const listItem = element("li");
        appendInlineMarkdown(listItem, item[1], sourceIndex);
        list.append(listItem);
        index += 1;
      }
      container.append(list);
      continue;
    }

    if (/^\s{0,3}(?:---+|___+|\*\*\*+)\s*$/.test(line)) {
      container.append(element("hr"));
      index += 1;
      continue;
    }

    const paragraphLines = [line.trim()];
    index += 1;
    while (index < lines.length && !isMarkdownBlockStart(lines[index])) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    const paragraph = element("p");
    appendInlineMarkdown(paragraph, paragraphLines.join(" "), sourceIndex);
    container.append(paragraph);
  }

  return container;
}

function showToast(message) {
  const toast = byId("global-feedback");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.setTimeout(() => toast.classList.add("hidden"), 4500);
}

function switchView(name) {
  if (!views.has(name)) return;
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
  byId(`view-${name}`).classList.add("active");
  document.querySelector(`[data-view="${name}"]`).classList.add("active");
  window.history.replaceState(null, "", `#${name}`);
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (name === "admin") loadAdministration({ promptOnUnauthorized: true });
}

function populateFilters(repositories) {
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
  const current = select.value;
  select.replaceChildren(new Option("Todas", ""));
  if (!project) return;
  const repository = state.repositories.find((item) => item.project === project);
  if (!repository) return;

  const allBranches = repository.branch_names || repository.canonical_branches || [];
  const canonical = new Set(repository.canonical_branches || []);
  const preferred = repository.preferred_branch || "";
  const grouped = new Set();

  function addGroup(label, branches) {
    const values = branches.filter((branch) => branch && !grouped.has(branch));
    if (!values.length) return;
    const group = document.createElement("optgroup");
    group.label = label;
    values.forEach((branch) => {
      grouped.add(branch);
      group.append(new Option(branch, branch));
    });
    select.append(group);
  }

  addGroup("Preferencial", preferred ? [preferred] : []);
  addGroup("Canônicas", allBranches.filter((branch) => canonical.has(branch)));
  addGroup("Outras branches", allBranches.filter((branch) => !canonical.has(branch)));
  select.value = allBranches.includes(current) ? current : preferred;
}

function scopeSummary(resolution) {
  if (!resolution?.automatic || !resolution.scopes?.length) return "";
  const values = resolution.scopes.map((scope) =>
    [scope.project, scope.branch].filter(Boolean).join(" · ")
  );
  return ` Escopo automático: ${values.join(" ↔ ")}.`;
}

async function loadCatalog() {
  const catalog = await api("/ui-api/repositories");
  state.repositories = catalog.repositories || [];
  populateFilters(state.repositories);
}

function resultCard(result, sourceId = "") {
  const card = element("article", "result-card");
  if (sourceId) {
    card.id = `source-${sourceId}`;
    card.tabIndex = -1;
  }
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
  const submit = event.submitter;
  feedback.textContent = "Buscando evidências…";
  results.replaceChildren();
  if (submit) submit.disabled = true;
  const payload = {
    query: byId("search-query").value.trim(),
    mode: byId("search-mode").value,
    limit: 10,
  };
  if (byId("search-project").value) payload.project = byId("search-project").value;
  if (byId("search-branch").value) payload.branch = byId("search-branch").value;
  try {
    const response = await api("/ui-api/search", { method: "POST", body: JSON.stringify(payload) });
    feedback.textContent = `${response.count} trechos encontrados.${scopeSummary(response.scope_resolution)}`;
    response.results.forEach((result) => results.append(resultCard(result)));
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    if (submit) submit.disabled = false;
  }
}

async function submitAsk(event) {
  event.preventDefault();
  const feedback = byId("ask-feedback");
  const card = byId("answer-card");
  const sources = byId("answer-sources");
  const submit = event.submitter;
  feedback.textContent = "Recuperando evidências e elaborando a resposta…";
  card.classList.add("hidden");
  card.replaceChildren();
  sources.replaceChildren();
  if (submit) submit.disabled = true;
  const payload = {
    query: byId("ask-query").value.trim(),
    mode: "hybrid",
    limit: 10,
  };
  if (byId("ask-project").value) payload.project = byId("ask-project").value;
  if (byId("ask-branch").value) payload.branch = byId("ask-branch").value;
  try {
    const response = await api("/ui-api/ask", { method: "POST", body: JSON.stringify(payload) });
    const resolution = response.context?.scope_resolution;
    const incompleteScopes = ["incomplete_scope_coverage", "scope_overclaim"].includes(
      response.grounding_status,
    );
    feedback.textContent = response.abstained
      ? `Não há evidência indexada suficiente.${scopeSummary(resolution)}`
      : `${incompleteScopes ? "Resposta parcial: nem todos os escopos foram citados." : "Resposta concluída."}${scopeSummary(resolution)}`;
    card.append(element("h3", "", response.abstained ? "Evidência insuficiente" : "Resposta"));
    card.append(renderMarkdown(
      response.answer || "A base indexada não sustenta uma resposta.",
      response.sources || [],
    ));
    const footer = element("div", "answer-footer");
    footer.append(element("span", "chip accent", response.grounding_status || "sem status"));
    footer.append(element("span", "chip", `${(response.citations_used || []).length} citações`));
    const exploration = response.context?.exploration;
    if (exploration?.intent === "overview") footer.append(element("span", "chip", "visão geral"));
    const scopeCoverage = response.scope_citation_coverage;
    if (scopeCoverage?.required) {
      footer.append(element(
        "span",
        "chip",
        `${scopeCoverage.cited_scopes?.length || 0}/${scopeCoverage.available_scopes?.length || 0} escopos citados`,
      ));
    }
    footer.append(element("span", "chip", `${response.duration_seconds || 0}s`));
    card.append(footer);
    card.classList.remove("hidden");
    (response.sources || []).forEach((source) => sources.append(resultCard(source, source.source_id)));
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    if (submit) submit.disabled = false;
  }
}

function showAdminAuth(message = "") {
  byId("admin-auth-error").textContent = message;
  byId("admin-auth-overlay").classList.remove("hidden");
  window.setTimeout(() => byId("admin-password").focus(), 0);
}

function hideAdminAuth() {
  byId("admin-auth-overlay").classList.add("hidden");
  byId("admin-auth-error").textContent = "";
  byId("admin-password").value = "";
}

function showAdminLocked() {
  state.adminAuthenticated = false;
  byId("admin-locked").classList.remove("hidden");
  byId("admin-content").classList.add("hidden");
  byId("admin-actions").classList.add("hidden");
}

function showAdminContent() {
  state.adminAuthenticated = true;
  byId("admin-locked").classList.add("hidden");
  byId("admin-content").classList.remove("hidden");
  byId("admin-actions").classList.remove("hidden");
}

function statusCard(label, value, detail, tone = "ok") {
  const card = element("article", `status-card ${tone}`);
  card.append(element("span", "", label));
  card.append(element("strong", "", value));
  card.append(element("small", "", detail));
  return card;
}

function renderServices(data) {
  const target = byId("admin-service-grid");
  target.replaceChildren();
  const repositoryChunks = (data.repositories || []).reduce((sum, item) => sum + Number(item.chunks || 0), 0);
  const embeddedChunks = (data.repositories || []).reduce((sum, item) => sum + Number(item.embedded_chunks || 0), 0);
  const coverage = repositoryChunks ? embeddedChunks / repositoryChunks : 0;
  const databaseOk = data.database?.status === "ok";
  const indexStatus = data.indexer?.status || "Não registrada";
  const indexTone = data.indexer?.status === "failed" ? "error" : (data.indexer?.status === "warning" ? "warning" : "ok");
  target.append(statusCard("API RAG", data.service?.status === "ok" ? "Disponível" : "Indisponível", `versão ${data.service?.version || "—"}`, data.service?.status === "ok" ? "ok" : "error"));
  target.append(statusCard("PostgreSQL", databaseOk ? "Conectado" : "Indisponível", `${formatNumber(data.database?.chunks)} chunks`, databaseOk ? "ok" : "error"));
  target.append(statusCard("Embeddings", formatPercent(coverage), `${formatNumber(embeddedChunks)} de ${formatNumber(repositoryChunks)} chunks`, coverage >= 0.99 ? "ok" : "warning"));
  target.append(statusCard("Gerador local", data.generation?.configured ? "Configurado" : "Não configurado", data.generation?.model || "sem modelo informado", data.generation?.configured ? "ok" : "warning"));
  target.append(statusCard("Indexação automática", formatStatus(indexStatus), data.indexer?.run_id || "sem execução registrada", indexTone));
  target.append(statusCard("Processo da API", `PID ${data.service?.process_id || "—"}`, `ativo há ${formatDuration(data.service?.uptime_seconds)}`, "ok"));
}

function appendDetail(target, label, value) {
  const row = element("div", "detail-row");
  row.append(element("span", "", label));
  row.append(element("span", "", value));
  target.append(row);
}

function renderMachine(machine) {
  const target = byId("machine-details");
  target.classList.remove("loading-block");
  target.replaceChildren();
  appendDetail(target, "Host", machine.hostname || "Não disponível");
  appendDetail(target, "Sistema", [machine.operating_system, machine.release, machine.architecture].filter(Boolean).join(" · ") || "Não disponível");
  appendDetail(target, "Python", machine.python || "Não disponível");
  appendDetail(target, "Processadores lógicos", String(machine.logical_cpus ?? "Não disponível"));
  const memory = machine.memory;
  appendDetail(target, "Memória", memory ? `${formatBytes(memory.used_bytes)} de ${formatBytes(memory.total_bytes)} (${memory.used_percent}%)` : "Não disponível");
  const disk = machine.disk;
  appendDetail(target, "Armazenamento", disk ? `${formatBytes(disk.used_bytes)} de ${formatBytes(disk.total_bytes)} (${disk.used_percent}%)` : "Não disponível");
  const gpus = machine.gpus || [];
  appendDetail(target, "GPU", gpus.length ? gpus.map((gpu) => `${gpu.name}: ${formatNumber(gpu.memory_used_mib)} de ${formatNumber(gpu.memory_total_mib)} MiB, ${gpu.utilization_percent}%, ${gpu.temperature_c} °C`).join("; ") : "Não detectada");
}

function renderRun(indexer) {
  const target = byId("run-summary");
  target.classList.remove("loading-block");
  target.replaceChildren();
  if (!indexer) {
    target.append(element("p", "", "Nenhuma execução registrada."));
    return;
  }
  const tone = indexer.status === "failed" ? "error" : (indexer.status === "warning" ? "warning" : "");
  const status = element("div", "run-status");
  status.append(element("span", `pulse ${tone}`));
  status.append(element("strong", "", formatStatus(indexer.status)));
  target.append(status);
  const details = element("div", "run-detail");
  [
    ["Run ID", indexer.run_id || "—"],
    ["Etapa", indexer.stage || indexer.last_event?.message || "Concluída"],
    ["Duração", indexer.duration_seconds == null ? "—" : formatDuration(indexer.duration_seconds)],
    ["Atualizado", indexer.updated_at ? new Date(indexer.updated_at).toLocaleString("pt-BR") : "—"],
  ].forEach(([label, value]) => {
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

function renderAdminRepositories(repositories) {
  const target = byId("admin-repository-list");
  target.classList.remove("loading-block");
  target.replaceChildren();
  repositories.forEach((repository) => {
    const row = element("article", "repository-row");
    const identity = element("div");
    identity.append(element("strong", "", repository.project));
    identity.append(element("small", "", repository.repository_id));
    row.append(identity);
    [
      ["Branches", repository.branches],
      ["Documentos", formatNumber(repository.documents)],
      ["Chunks", formatNumber(repository.chunks)],
      ["Vetores", formatPercent(repository.embedding_coverage)],
    ].forEach(([label, value]) => {
      const cell = element("div", "repository-stat");
      cell.append(element("strong", "", String(value)));
      cell.append(element("small", "", label));
      row.append(cell);
    });
    target.append(row);
  });
  if (!repositories.length) target.append(element("p", "", "Nenhum repositório disponível."));
}

function renderAdministration(data) {
  showAdminContent();
  renderServices(data);
  renderMachine(data.machine || {});
  renderRun(data.indexer);
  renderAdminRepositories(data.repositories || []);
}

async function loadAdministration({ promptOnUnauthorized = false } = {}) {
  try {
    const data = await api("/ui-api/admin/status");
    renderAdministration(data);
  } catch (error) {
    if (error.status === 401) {
      showAdminLocked();
      if (promptOnUnauthorized) showAdminAuth();
      return;
    }
    showAdminLocked();
    showToast(error.message);
  }
}

async function submitAdminLogin(event) {
  event.preventDefault();
  const submit = event.submitter;
  if (submit) submit.disabled = true;
  try {
    await api("/ui-api/admin/session", {
      method: "POST",
      body: JSON.stringify({ password: byId("admin-password").value }),
    });
    hideAdminAuth();
    await loadAdministration();
  } catch (error) {
    byId("admin-auth-error").textContent = error.message;
  } finally {
    if (submit) submit.disabled = false;
  }
}

async function logoutAdministration() {
  try {
    await api("/ui-api/admin/session", { method: "DELETE" });
  } catch (_) { /* a sessão local será encerrada mesmo sem resposta */ }
  showAdminLocked();
  showToast("Sessão administrativa encerrada.");
}

async function bootstrap() {
  const requestedView = window.location.hash.slice(1);
  if (views.has(requestedView)) switchView(requestedView);
  try {
    await loadCatalog();
  } catch (error) {
    showToast(`Não foi possível carregar os projetos: ${error.message}`);
  }
}

document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
byId("search-project").addEventListener("change", () => updateBranches("search-project", "search-branch"));
byId("ask-project").addEventListener("change", () => updateBranches("ask-project", "ask-branch"));
byId("search-form").addEventListener("submit", submitSearch);
byId("ask-form").addEventListener("submit", submitAsk);
byId("admin-login-button").addEventListener("click", () => showAdminAuth());
byId("admin-auth-form").addEventListener("submit", submitAdminLogin);
byId("admin-auth-cancel").addEventListener("click", hideAdminAuth);
byId("admin-refresh").addEventListener("click", () => loadAdministration().catch((error) => showToast(error.message)));
byId("admin-logout").addEventListener("click", logoutAdministration);

bootstrap();
