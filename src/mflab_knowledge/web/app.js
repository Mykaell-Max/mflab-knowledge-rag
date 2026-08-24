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
const investigationToolLabels = {
  search_code: "buscar no código",
  find_symbol: "localizar símbolo",
  open_neighborhood: "abrir vizinhança",
  open_related: "abrir relações",
  find_callers: "localizar chamadores",
  find_callees: "seguir chamadas",
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

const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

function investigationDetail(step) {
  const data = step?.data || {};
  const details = [];
  if (Array.isArray(data.scopes) && data.scopes.length) {
    details.push(data.scopes.map((scope) => `${scope.project} / ${scope.branch}`).join("; "));
  }
  if (Number.isFinite(Number(data.candidates))) details.push(`${formatNumber(data.candidates)} candidatos`);
  if (Number.isFinite(Number(data.sources))) details.push(`${formatNumber(data.sources)} fontes selecionadas`);
  if (Number.isFinite(Number(data.maps))) details.push(`${formatNumber(data.maps)} mapas estruturais`);
  if (Array.isArray(data.queries) && data.queries.length) {
    details.push(`Consultas: ${data.queries.join("; ")}`);
  }
  if (Array.isArray(data.terms) && data.terms.length) {
    details.push(`Navegação: ${data.terms.join("; ")}`);
  }
  if (Number.isFinite(Number(data.nodes))) details.push(`${formatNumber(data.nodes)} relações estruturais`);
  if (Number.isFinite(Number(data.evidence))) details.push(`${formatNumber(data.evidence)} trechos primários`);
  if (Number.isFinite(Number(data.new_evidence))) details.push(`${formatNumber(data.new_evidence)} novas evidências`);
  if (Array.isArray(data.actions) && data.actions.length) {
    const actions = data.actions.map((action) => {
      const value = action.query || action.chunk_id || "";
      const count = action.result_count === undefined ? "" : ` (${action.result_count} resultados)`;
      const label = investigationToolLabels[action.tool] || action.tool;
      return `${label}: ${value}${count}`;
    });
    details.push(`Ações: ${actions.join("; ")}`);
  }
  if (data.coverage && typeof data.coverage === "object") {
    details.push(
      `Cobertura: ${formatNumber(data.coverage.covered)} cobertos, `
      + `${formatNumber(data.coverage.partial)} parciais, ${formatNumber(data.coverage.gap)} lacunas`,
    );
  }
  if (Number.isFinite(Number(data.supported))) {
    details.push(`${formatNumber(data.supported)} afirmações sustentadas`);
  }
  const revised = Number(data.unsupported || 0) + Number(data.uncertain || 0);
  if (revised > 0) details.push(`${formatNumber(revised)} afirmações encaminhadas para revisão`);
  return [step?.detail, ...details].filter(Boolean).join(" ");
}

function renderInvestigation(steps, running = false) {
  const panel = byId("ask-investigation");
  const list = byId("ask-investigation-steps");
  const time = byId("ask-investigation-time");
  const hasSteps = Boolean((steps || []).length);
  const hadSteps = panel.dataset.hasSteps === "true";
  if (hasSteps && !hadSteps) panel.open = true;
  if (!hasSteps) panel.open = false;
  panel.dataset.hasSteps = String(hasSteps);
  list.replaceChildren();
  (steps || []).forEach((step, index) => {
    const item = element(
      "li",
      `investigation-step${running && index === steps.length - 1 ? " current" : ""}`,
    );
    item.append(element("strong", "", step.title || "Etapa concluída"));
    const detail = investigationDetail(step);
    if (detail) item.append(element("p", "", detail));
    list.append(item);
  });
  const last = (steps || [])[steps.length - 1];
  time.textContent = last ? formatDuration(last.elapsed_seconds || 0) : "";
  panel.classList.toggle("hidden", !hasSteps);
}

function updateInvestigationToggleLabel() {
  const panel = byId("ask-investigation");
  byId("ask-investigation-toggle-label").textContent = panel.open
    ? "Ocultar etapas"
    : "Mostrar etapas";
}

const SVG_NS = "http://www.w3.org/2000/svg";
const GRAPH_VIEW_WIDTH = 1100;
const GRAPH_VIEW_HEIGHT = 520;
const GRAPH_MIN_SCALE = 0.35;
const GRAPH_MAX_SCALE = 2.6;

function svgElement(name, attributes = {}, text = "") {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
  if (text) node.textContent = text;
  return node;
}

function shortGraphText(value, limit) {
  const text = String(value || "");
  return text.length <= limit ? text : `${text.slice(0, Math.max(1, limit - 1))}…`;
}

function graphPositions(nodes, edges) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const incoming = new Map(nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(nodes.map((node) => [node.id, []]));
  edges.forEach((edge) => {
    if (!byId.has(edge.source) || !byId.has(edge.target)) return;
    incoming.set(edge.target, (incoming.get(edge.target) || 0) + 1);
    outgoing.get(edge.source).push(edge.target);
  });
  const level = new Map();
  const queue = nodes.filter((node) => incoming.get(node.id) === 0).map((node) => node.id);
  if (!queue.length && nodes.length) queue.push(nodes[0].id);
  queue.forEach((id) => level.set(id, 0));
  while (queue.length) {
    const current = queue.shift();
    const nextLevel = Math.min(4, (level.get(current) || 0) + 1);
    (outgoing.get(current) || []).forEach((target) => {
      if (level.has(target)) return;
      level.set(target, nextLevel);
      queue.push(target);
    });
  }
  nodes.forEach((node) => {
    if (!level.has(node.id)) level.set(node.id, 0);
  });
  const columns = new Map();
  nodes.forEach((node) => {
    const value = level.get(node.id) || 0;
    if (!columns.has(value)) columns.set(value, []);
    columns.get(value).push(node);
  });
  const positions = new Map();
  [...columns.entries()].sort((left, right) => left[0] - right[0]).forEach(([column, values]) => {
    values.forEach((node, row) => positions.set(node.id, { x: 32 + column * 238, y: 32 + row * 102 }));
  });
  return {
    positions,
    width: Math.max(820, 64 + (Math.max(...level.values(), 0) + 1) * 258),
    height: Math.max(300, 64 + Math.max(...[...columns.values()].map((items) => items.length), 1) * 108),
  };
}

function bindGraphNavigation(canvas, svg, stage, layout) {
  const transform = { x: 0, y: 0, scale: 1 };
  let drag = null;

  function applyTransform() {
    stage.setAttribute(
      "transform",
      `translate(${transform.x} ${transform.y}) scale(${transform.scale})`,
    );
  }

  function readableView() {
    transform.scale = Math.min(1, (GRAPH_VIEW_WIDTH - 72) / layout.width);
    transform.x = Math.max(28, (GRAPH_VIEW_WIDTH - layout.width * transform.scale) / 2);
    transform.y = 28;
    applyTransform();
  }

  function fitView() {
    transform.scale = Math.max(
      GRAPH_MIN_SCALE,
      Math.min(
        1,
        (GRAPH_VIEW_WIDTH - 72) / layout.width,
        (GRAPH_VIEW_HEIGHT - 72) / layout.height,
      ),
    );
    transform.x = (GRAPH_VIEW_WIDTH - layout.width * transform.scale) / 2;
    transform.y = (GRAPH_VIEW_HEIGHT - layout.height * transform.scale) / 2;
    applyTransform();
  }

  function graphPoint(clientX, clientY) {
    const bounds = svg.getBoundingClientRect();
    return {
      x: (clientX - bounds.left) * GRAPH_VIEW_WIDTH / Math.max(bounds.width, 1),
      y: (clientY - bounds.top) * GRAPH_VIEW_HEIGHT / Math.max(bounds.height, 1),
    };
  }

  function zoomAt(factor, point = { x: GRAPH_VIEW_WIDTH / 2, y: GRAPH_VIEW_HEIGHT / 2 }) {
    const previous = transform.scale;
    const next = Math.max(GRAPH_MIN_SCALE, Math.min(GRAPH_MAX_SCALE, previous * factor));
    const ratio = next / previous;
    transform.x = point.x - (point.x - transform.x) * ratio;
    transform.y = point.y - (point.y - transform.y) * ratio;
    transform.scale = next;
    applyTransform();
  }

  canvas.onwheel = (event) => {
    event.preventDefault();
    zoomAt(event.deltaY < 0 ? 1.14 : 1 / 1.14, graphPoint(event.clientX, event.clientY));
  };
  canvas.onpointerdown = (event) => {
    if (event.target.closest?.(".graph-node")) return;
    drag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    canvas.setPointerCapture(event.pointerId);
    canvas.classList.add("dragging");
  };
  canvas.onpointermove = (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const bounds = svg.getBoundingClientRect();
    transform.x += (event.clientX - drag.x) * GRAPH_VIEW_WIDTH / Math.max(bounds.width, 1);
    transform.y += (event.clientY - drag.y) * GRAPH_VIEW_HEIGHT / Math.max(bounds.height, 1);
    drag.x = event.clientX;
    drag.y = event.clientY;
    applyTransform();
  };
  const stopDragging = (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag = null;
    canvas.classList.remove("dragging");
  };
  canvas.onpointerup = stopDragging;
  canvas.onpointercancel = stopDragging;

  byId("answer-graph-zoom-in").onclick = () => zoomAt(1.2);
  byId("answer-graph-zoom-out").onclick = () => zoomAt(1 / 1.2);
  byId("answer-graph-fit").onclick = fitView;
  readableView();
}

function renderInvestigationGraph(graph) {
  const panel = byId("answer-graph");
  const canvas = byId("answer-graph-canvas");
  const summary = byId("answer-graph-summary");
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph?.edges) ? graph.edges : [];
  canvas.replaceChildren();
  if (graph?.status !== "available" || !nodes.length || !edges.length) {
    panel.classList.add("hidden");
    panel.open = false;
    summary.textContent = "";
    return;
  }

  const visibleIds = new Set(nodes.map((node) => node.id));
  const visibleEdges = edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target));
  const layout = graphPositions(nodes, visibleEdges);
  const svg = svgElement("svg", {
    class: "investigation-graph",
    viewBox: `0 0 ${GRAPH_VIEW_WIDTH} ${GRAPH_VIEW_HEIGHT}`,
    preserveAspectRatio: "xMidYMid meet",
    role: "img",
    "aria-label": `${nodes.length} nós e ${visibleEdges.length} relações estruturais`,
  });
  const defs = svgElement("defs");
  const marker = svgElement("marker", {
    id: "graph-arrow",
    viewBox: "0 0 10 10",
    refX: 9,
    refY: 5,
    markerWidth: 6,
    markerHeight: 6,
    orient: "auto-start-reverse",
  });
  marker.append(svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#0011ad" }));
  defs.append(marker);
  svg.append(defs);
  const stage = svgElement("g", { class: "graph-stage" });
  svg.append(stage);

  const edgeLabels = { calls: "chama", related: "relacionado", neighbor: "vizinhança" };
  visibleEdges.forEach((edge) => {
    const source = layout.positions.get(edge.source);
    const target = layout.positions.get(edge.target);
    if (!source || !target) return;
    const x1 = source.x + 188;
    const y1 = source.y + 34;
    const x2 = target.x;
    const y2 = target.y + 34;
    const line = svgElement("path", {
      d: `M ${x1} ${y1} C ${x1 + 28} ${y1}, ${x2 - 28} ${y2}, ${x2} ${y2}`,
      class: `graph-edge ${edge.kind || "related"}`,
    });
    if (edge.directed) line.setAttribute("marker-end", "url(#graph-arrow)");
    stage.append(line);
    stage.append(svgElement(
      "text",
      { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 5, class: "graph-edge-label" },
      edgeLabels[edge.kind] || edge.kind || "relação",
    ));
  });

  nodes.forEach((node) => {
    const position = layout.positions.get(node.id);
    if (!position) return;
    const linked = Boolean(node.source_id);
    const group = svgElement("g", {
      class: `graph-node${linked ? " linked" : ""}`,
      transform: `translate(${position.x} ${position.y})`,
      role: linked ? "link" : "group",
      "aria-label": [node.label, node.project, node.branch].filter(Boolean).join(", "),
    });
    group.append(svgElement("rect", { width: 188, height: 68, rx: 4 }));
    group.append(svgElement("text", { x: 10, y: 20, class: "graph-node-title" }, shortGraphText(node.label, 25)));
    group.append(svgElement("text", { x: 10, y: 39, class: "graph-node-path" }, shortGraphText(node.path, 34)));
    group.append(svgElement(
      "text",
      { x: 10, y: 56, class: "graph-node-scope" },
      shortGraphText([node.project, node.branch].filter(Boolean).join(" · "), 32),
    ));
    if (linked) {
      group.tabIndex = 0;
      const openSource = () => {
        const source = document.getElementById(`source-${node.source_id}`);
        if (source) {
          source.scrollIntoView({ behavior: "smooth", block: "center" });
          source.focus({ preventScroll: true });
        }
      };
      group.addEventListener("click", openSource);
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") openSource();
      });
    }
    stage.append(group);
  });
  canvas.append(svg);
  bindGraphNavigation(canvas, svg, stage, layout);
  summary.textContent = `${formatNumber(nodes.length)} nós · ${formatNumber(visibleEdges.length)} relações`;
  panel.classList.remove("hidden");
  panel.open = true;
}

async function runAskJob(payload) {
  const created = await api("/ui-api/ask-jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  while (true) {
    const job = await api(`/ui-api/ask-jobs/${encodeURIComponent(created.job_id)}`);
    renderInvestigation(job.steps || [], job.status !== "completed" && job.status !== "failed");
    if (job.status === "completed") return job.result;
    if (job.status === "failed") throw new Error(job.error || "A investigação não pôde ser concluída.");
    await wait(400);
  }
}

function element(name, className, text) {
  const node = document.createElement(name);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const languageAliases = {
  "c++": "cpp", cxx: "cpp", cc: "cpp", hpp: "cpp", hxx: "cpp",
  c: "c", h: "c", cpp: "cpp", cuda: "cpp", cu: "cpp",
  f: "fortran", f90: "fortran", f95: "fortran", f03: "fortran", f08: "fortran", fortran: "fortran",
  py: "python", python: "python",
  sh: "shell", bash: "shell", shell: "shell", dockerfile: "shell",
  js: "javascript", jsx: "javascript", javascript: "javascript",
  ts: "typescript", tsx: "typescript", typescript: "typescript",
  json: "json", yaml: "yaml", yml: "yaml", toml: "toml",
  cmake: "cmake", make: "make", makefile: "make",
  sql: "sql", text: "text", txt: "text", markdown: "text", md: "text",
};

const formatLanguages = {
  c: "c", cpp: "cpp", cpp_header: "cpp", fortran: "fortran",
  python: "python", shell: "shell", dockerfile: "shell",
  json: "json", yaml: "yaml", toml: "toml", cmake: "cmake", make: "make",
};

const extensionLanguages = {
  c: "c", h: "c", cc: "cpp", cpp: "cpp", cxx: "cpp", hpp: "cpp", hxx: "cpp",
  cu: "cpp", cuh: "cpp", f: "fortran", f90: "fortran", f95: "fortran",
  f03: "fortran", f08: "fortran", py: "python", sh: "shell", bash: "shell",
  js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
  json: "json", yaml: "yaml", yml: "yaml", toml: "toml", cmake: "cmake", sql: "sql",
};

const keywordGroups = {
  c: "auto break case const continue default do else enum extern for goto if inline register restrict return sizeof static struct switch typedef union volatile while",
  cpp: "alignas alignof and and_eq asm bitand bitor break case catch class compl concept const consteval constexpr constinit const_cast continue co_await co_return co_yield decltype default delete do dynamic_cast else enum explicit export extern for friend goto if inline mutable namespace new noexcept not not_eq operator or or_eq private protected public register reinterpret_cast requires return sizeof static static_assert static_cast struct switch template this thread_local throw try typedef typeid typename union using virtual volatile while xor xor_eq",
  fortran: "allocate allocatable associate backspace block call case character class close common contains continue cycle data deallocate dimension do else elseif elsewhere end enddo endif entry enum equivalence error exit external final flush forall format function generic goto if implicit import in include inquire intent interface intrinsic module namelist none nullify only open operator optional parameter pause pointer print private procedure program protected public pure read recursive result return rewind save select sequence stop submodule subroutine target then use value volatile wait where while write",
  python: "and as assert async await break case class continue def del elif else except finally for from global if import in is lambda match nonlocal not or pass raise return try while with yield",
  shell: "case do done elif else esac fi for function if in select then time until while",
  javascript: "async await break case catch class const continue debugger default delete do else export extends finally for from function get if import in instanceof let new of return set static super switch this throw try typeof var void while with yield",
  typescript: "abstract any as asserts async await bigint boolean break case catch class const constructor continue debugger declare default delete do else enum export extends finally for from function get if implements import in infer instanceof interface is keyof let module namespace never new null number object of override private protected public readonly require return set static string super switch symbol this throw try type typeof undefined unique unknown var void while with yield",
  cmake: "and break cache command continue elseif else endforeach endfunction endif endmacro endwhile false function if macro not or parent_scope return set true unset while",
  make: "define else endef endif export ifdef ifeq ifndef ifneq include override private sinclude undefine unexport vpath",
  sql: "all alter and any as asc begin between by case check column commit constraint create database default delete desc distinct drop else end exists foreign from full grant group having in index inner insert into is join key left like limit not null on or order outer primary references right rollback select set table then union unique update values view when where with",
};

const keywords = Object.fromEntries(
  Object.entries(keywordGroups).map(([language, words]) => [language, new Set(words.split(" "))]),
);
keywords.cpp = new Set([...keywords.c, ...keywords.cpp]);

const typeWords = new Set(
  "bool byte char character complex double float int integer logical long real short signed size_t string unsigned void wchar_t".split(" "),
);
const literalWords = new Set("false none null nullptr true undefined".split(" "));

function normalizeLanguage(language) {
  const value = String(language || "text").trim().toLowerCase();
  return languageAliases[value] || (languageAliases[value.replace(/^language-/, "")] || "text");
}

function languageForResult(result) {
  const byFormat = formatLanguages[String(result.format || "").toLowerCase()];
  if (byFormat) return byFormat;
  const path = String(result.path || "").toLowerCase();
  const filename = path.split("/").pop() || "";
  if (filename === "cmakelists.txt") return "cmake";
  if (filename === "makefile" || filename.startsWith("makefile.")) return "make";
  if (filename === "dockerfile" || filename.startsWith("dockerfile.")) return "shell";
  const extension = filename.includes(".") ? filename.split(".").pop() : "";
  return extensionLanguages[extension] || "text";
}

function syntaxToken(target, className, text) {
  if (!text) return;
  target.append(className ? element("span", className, text) : document.createTextNode(text));
}

function commentRules(language) {
  if (["c", "cpp", "javascript", "typescript"].includes(language)) {
    return { line: "//", blockStart: "/*", blockEnd: "*/" };
  }
  if (["python", "shell", "cmake", "make", "yaml", "toml"].includes(language)) {
    return { line: "#" };
  }
  if (language === "fortran") return { line: "!" };
  if (language === "sql") return { line: "--", blockStart: "/*", blockEnd: "*/" };
  return {};
}

function highlightCode(target, code, requestedLanguage) {
  const language = normalizeLanguage(requestedLanguage);
  const rules = commentRules(language);
  const words = keywords[language] || new Set();
  const source = String(code || "");
  let index = 0;
  let lineStart = true;

  while (index < source.length) {
    const remaining = source.slice(index);
    const whitespace = remaining.match(/^\s+/);
    if (whitespace) {
      syntaxToken(target, "", whitespace[0]);
      lineStart = whitespace[0].includes("\n")
        ? !whitespace[0].slice(whitespace[0].lastIndexOf("\n") + 1).trim()
        : lineStart;
      index += whitespace[0].length;
      continue;
    }

    if (["c", "cpp"].includes(language) && lineStart && source[index] === "#") {
      const end = source.indexOf("\n", index);
      const length = (end < 0 ? source.length : end) - index;
      syntaxToken(target, "syntax-meta", source.slice(index, index + length));
      index += length;
      lineStart = false;
      continue;
    }

    if (rules.blockStart && source.startsWith(rules.blockStart, index)) {
      const end = source.indexOf(rules.blockEnd, index + rules.blockStart.length);
      const stop = end < 0 ? source.length : end + rules.blockEnd.length;
      syntaxToken(target, "syntax-comment", source.slice(index, stop));
      lineStart = source.slice(index, stop).endsWith("\n");
      index = stop;
      continue;
    }

    if (rules.line && source.startsWith(rules.line, index)) {
      const end = source.indexOf("\n", index);
      const stop = end < 0 ? source.length : end;
      syntaxToken(target, "syntax-comment", source.slice(index, stop));
      index = stop;
      lineStart = false;
      continue;
    }

    const quote = source[index];
    if (quote === "\"" || quote === "'" || (quote === "`" && ["javascript", "typescript"].includes(language))) {
      let stop = index + 1;
      while (stop < source.length) {
        if (source[stop] === "\\") {
          stop += 2;
          continue;
        }
        if (source[stop] === quote) {
          if (source[stop + 1] === quote) {
            stop += 2;
            continue;
          }
          stop += 1;
          break;
        }
        stop += 1;
      }
      const after = source.slice(stop).match(/^\s*/)?.[0].length || 0;
      const isProperty = ["json", "yaml", "toml"].includes(language)
        && [":", "="].includes(source[stop + after]);
      syntaxToken(target, isProperty ? "syntax-property" : "syntax-string", source.slice(index, stop));
      lineStart = false;
      index = stop;
      continue;
    }

    const number = remaining.match(/^(?:0x[\da-f]+|0b[01]+|\d+(?:\.\d*)?(?:e[+-]?\d+)?)/i);
    if (number) {
      syntaxToken(target, "syntax-number", number[0]);
      index += number[0].length;
      lineStart = false;
      continue;
    }

    const identifier = remaining.match(/^[A-Za-z_][A-Za-z0-9_]*/);
    if (identifier) {
      const value = identifier[0];
      const folded = value.toLowerCase();
      const tail = source.slice(index + value.length);
      const next = tail.match(/^\s*(.)/)?.[1] || "";
      let tokenClass = "";
      if (literalWords.has(folded)) tokenClass = "syntax-literal";
      else if (typeWords.has(folded)) tokenClass = "syntax-type";
      else if (words.has(folded)) tokenClass = "syntax-keyword";
      else if (["json", "yaml", "toml"].includes(language) && [":", "="].includes(next)) tokenClass = "syntax-property";
      else if (next === "(") tokenClass = "syntax-function";
      syntaxToken(target, tokenClass, value);
      index += value.length;
      lineStart = false;
      continue;
    }

    const operator = remaining.match(/^(?:===|!==|<<=|>>=|=>|::|->|\*\*|\/\/|==|!=|<=|>=|&&|\|\||\+\+|--|\+=|-=|\*=|\/=|%=|<<|>>|[-+*/%=&|!<>^~?:]+)/);
    if (operator) {
      syntaxToken(target, "syntax-operator", operator[0]);
      index += operator[0].length;
      lineStart = false;
      continue;
    }

    const punctuation = remaining.match(/^[{}()[\];,.]+/);
    if (punctuation) {
      syntaxToken(target, "syntax-punctuation", punctuation[0]);
      index += punctuation[0].length;
      lineStart = false;
      continue;
    }

    syntaxToken(target, "", source[index]);
    lineStart = false;
    index += 1;
  }
}

function createCodeBlock(code, requestedLanguage, compact = false) {
  const language = normalizeLanguage(requestedLanguage);
  const block = element("div", `code-block${compact ? " compact" : ""}`);
  block.append(element("div", "code-language", language));
  const pre = element("pre");
  const codeNode = element("code", `language-${language}`);
  highlightCode(codeNode, code, language);
  pre.append(codeNode);
  block.append(pre);
  return block;
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
      container.append(createCodeBlock(codeLines.join("\n"), language));
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

function normalizedScopeText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function queryMentionsRepository(query, repository) {
  const normalizedQuery = ` ${normalizedScopeText(query)} `;
  return [repository.project, ...(repository.aliases || [])].some((value) => {
    const candidate = normalizedScopeText(value);
    return candidate.length > 1 && normalizedQuery.includes(` ${candidate} `);
  });
}

function explicitProjectConflict(query, selectedProject) {
  if (!selectedProject) return "";
  const mentioned = state.repositories.filter((repository) =>
    queryMentionsRepository(query, repository)
  );
  if (!mentioned.length || mentioned.some((repository) => repository.project === selectedProject)) {
    return "";
  }
  const names = mentioned.map((repository) => repository.project).join(", ");
  return `A consulta menciona ${names}, mas o filtro está em ${selectedProject}. `
    + "Corrija o projeto ou escolha Todos para usar o roteamento automático.";
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
  if (result.text) {
    const language = languageForResult(result);
    card.append(
      language === "text"
        ? element("p", "", result.text)
        : createCodeBlock(result.text, language, true),
    );
  }
  card.append(element("p", "citation", result.citation || ""));
  return card;
}

async function submitSearch(event) {
  event.preventDefault();
  const feedback = byId("search-feedback");
  const results = byId("search-results");
  const submit = event.submitter;
  const conflict = explicitProjectConflict(
    byId("search-query").value,
    byId("search-project").value,
  );
  if (conflict) {
    feedback.textContent = conflict;
    return;
  }
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
  const conflict = explicitProjectConflict(
    byId("ask-query").value,
    byId("ask-project").value,
  );
  if (conflict) {
    feedback.textContent = conflict;
    return;
  }
  feedback.textContent = "Recuperando evidências e elaborando a resposta…";
  card.classList.add("hidden");
  card.replaceChildren();
  sources.replaceChildren();
  renderInvestigation([]);
  renderInvestigationGraph(null);
  if (submit) submit.disabled = true;
  const responseDepth = byId("ask-response-depth").value;
  const payload = {
    query: byId("ask-query").value.trim(),
    mode: "hybrid",
    limit: responseDepth === "detailed" ? 14 : 10,
    temperature: 0,
    response_depth: responseDepth,
  };
  if (byId("ask-project").value) payload.project = byId("ask-project").value;
  if (byId("ask-branch").value) payload.branch = byId("ask-branch").value;
  try {
    const response = await runAskJob(payload);
    const resolution = response.context?.scope_resolution;
    const incompleteScopes = ["incomplete_scope_coverage", "scope_overclaim"].includes(
      response.grounding_status,
    );
    const unsupported = response.reason === "evidence_not_supported";
    feedback.textContent = response.abstained
      ? `${unsupported ? "As fontes recuperadas não sustentaram uma resposta conclusiva." : "Não há evidência indexada suficiente."}${scopeSummary(resolution)}`
      : `${incompleteScopes ? "Resposta concluída; confira os escopos citados." : "Resposta concluída."}${scopeSummary(resolution)}`;
    card.append(element("h3", "", response.abstained ? "Não foi possível concluir" : "Resposta"));
    card.append(renderMarkdown(
      response.answer || "A base indexada não sustenta uma resposta.",
      response.sources || [],
    ));
    const footer = element("div", "answer-footer");
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
    renderInvestigationGraph(response.investigation_graph);
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
byId("ask-investigation").addEventListener("toggle", updateInvestigationToggleLabel);
byId("admin-login-button").addEventListener("click", () => showAdminAuth());
byId("admin-auth-form").addEventListener("submit", submitAdminLogin);
byId("admin-auth-cancel").addEventListener("click", hideAdminAuth);
byId("admin-refresh").addEventListener("click", () => loadAdministration().catch((error) => showToast(error.message)));
byId("admin-logout").addEventListener("click", logoutAdministration);

bootstrap();
