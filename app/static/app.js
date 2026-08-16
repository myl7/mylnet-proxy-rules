"use strict";

const state = {
  meta: null,
  rules: [],
  revision: null,
  dirty: false,
  rowErrors: new Map(),
  jobId: null,
  jobTimer: null,
  running: false,
};

const el = {
  rows: document.getElementById("rows"),
  meta: document.getElementById("meta"),
  banner: document.getElementById("banner"),
  count: document.getElementById("count"),
  add: document.getElementById("add"),
  reload: document.getElementById("reload"),
  dryRun: document.getElementById("dry-run"),
  apply: document.getElementById("apply"),
  job: document.getElementById("job"),
  jobState: document.getElementById("job-state"),
  jobLog: document.getElementById("job-log"),
  jobToggle: document.getElementById("job-toggle"),
};

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw { status: response.status, detail: body && body.detail };
  }
  return body;
}

function specFor(type) {
  return state.meta.rule_types.find((item) => item.name === type) || state.meta.rule_types[0];
}

function defaultTarget() {
  return state.meta.targets.includes("proxy") ? "proxy" : state.meta.targets[0];
}

function setDirty(dirty) {
  state.dirty = dirty;
  el.apply.classList.toggle("dirty", dirty);
}

function showBanner(message, ok) {
  el.banner.textContent = message;
  el.banner.classList.toggle("ok", Boolean(ok));
  el.banner.hidden = !message;
}

function option(value, label) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label || value;
  return node;
}

function iconButton(label, title, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon";
  button.textContent = label;
  button.title = title;
  button.addEventListener("click", onClick);
  return button;
}

function buildRow(rule, index) {
  const row = document.createElement("div");
  row.className = "row";

  const position = document.createElement("span");
  position.className = "index";
  position.textContent = String(index + 1);
  row.append(position);

  const type = document.createElement("select");
  state.meta.rule_types.forEach((spec) => type.append(option(spec.name)));
  if (!state.meta.rule_types.some((spec) => spec.name === rule.type)) {
    type.append(option(rule.type, `${rule.type} (unknown)`));
  }
  type.value = rule.type;
  row.append(type);

  const payload = document.createElement("input");
  payload.className = "payload";
  payload.value = rule.payload;
  payload.spellcheck = false;
  payload.autocapitalize = "off";
  payload.autocomplete = "off";
  row.append(payload);

  const target = document.createElement("select");
  state.meta.targets.forEach((name) => target.append(option(name)));
  if (!state.meta.targets.includes(rule.target)) {
    target.append(option(rule.target, `${rule.target} (unknown)`));
  }
  target.value = rule.target;
  row.append(target);

  const note = document.createElement("input");
  note.value = rule.note;
  note.placeholder = "note";
  row.append(note);

  const actions = document.createElement("div");
  actions.className = "row-actions";

  const resolve = document.createElement("label");
  resolve.className = "resolve";
  const resolveBox = document.createElement("input");
  resolveBox.type = "checkbox";
  resolveBox.checked = rule.options === state.meta.no_resolve;
  resolve.append(resolveBox, document.createTextNode(state.meta.no_resolve));
  actions.append(resolve);

  actions.append(
    iconButton("⤒", "Move to top", () => moveTo(index, 0)),
    iconButton("⤓", "Move to bottom", () => moveTo(index, state.rules.length - 1)),
    iconButton("↑", "Move up", () => move(index, -1)),
    iconButton("↓", "Move down", () => move(index, 1)),
    iconButton("✕", "Delete", () => remove(index)),
  );
  row.append(actions);

  const applySpec = () => {
    const spec = specFor(type.value);
    payload.placeholder = spec.placeholder;
    payload.title = spec.hint;
    resolve.hidden = !spec.allows_no_resolve;
    if (!spec.allows_no_resolve && resolveBox.checked) {
      resolveBox.checked = false;
      rule.options = "";
    }
  };
  applySpec();

  type.addEventListener("change", () => {
    rule.type = type.value;
    applySpec();
    setDirty(true);
  });
  payload.addEventListener("input", () => {
    rule.payload = payload.value;
    setDirty(true);
  });
  target.addEventListener("change", () => {
    rule.target = target.value;
    setDirty(true);
  });
  note.addEventListener("input", () => {
    rule.note = note.value;
    setDirty(true);
  });
  resolveBox.addEventListener("change", () => {
    rule.options = resolveBox.checked ? state.meta.no_resolve : "";
    setDirty(true);
  });

  const messages = state.rowErrors.get(index);
  if (messages) {
    row.classList.add("invalid");
    const problem = document.createElement("div");
    problem.className = "row-error";
    problem.textContent = messages.join("; ");
    row.append(problem);
  }

  return row;
}

function render() {
  el.rows.replaceChildren(...state.rules.map(buildRow));
  el.count.textContent = `${state.rules.length} rules, limit ${state.meta.max_rules}`;
}

function move(index, delta) {
  const next = index + delta;
  if (next < 0 || next >= state.rules.length) {
    return;
  }
  [state.rules[index], state.rules[next]] = [state.rules[next], state.rules[index]];
  state.rowErrors.clear();
  setDirty(true);
  render();
}

function moveTo(index, target) {
  if (index === target) {
    return;
  }
  const [rule] = state.rules.splice(index, 1);
  state.rules.splice(target, 0, rule);
  state.rowErrors.clear();
  setDirty(true);
  render();
}

function remove(index) {
  state.rules.splice(index, 1);
  state.rowErrors.clear();
  setDirty(true);
  render();
}

function add() {
  state.rules.unshift({
    type: "DOMAIN-SUFFIX",
    payload: "",
    target: defaultTarget(),
    options: "",
    note: "",
  });
  state.rowErrors.clear();
  setDirty(true);
  render();
  const first = el.rows.querySelector("input.payload");
  if (first) {
    first.focus();
  }
}

// Leaves the banner alone, so a caller can report the outcome that triggered the reload.
async function loadRules() {
  const data = await api("/api/rules");
  state.rules = data.rules;
  state.revision = data.revision;
  state.rowErrors.clear();
  setDirty(false);
  render();
}

function setBusy(busy) {
  state.running = busy;
  [el.apply, el.dryRun, el.reload, el.add].forEach((button) => {
    button.disabled = busy;
  });
}

function showJob(job) {
  el.job.hidden = false;
  el.jobState.className = `job-state ${job.state}`;
  const label = job.kind === "check" ? "Dry run" : "Apply";
  el.jobState.textContent = `${label}: ${job.state}${job.error ? ` — ${job.error}` : ""}`;
  const atBottom = el.jobLog.scrollTop + el.jobLog.clientHeight >= el.jobLog.scrollHeight - 20;
  el.jobLog.textContent = (job.log || []).join("\n");
  if (atBottom) {
    el.jobLog.scrollTop = el.jobLog.scrollHeight;
  }
}

async function pollJob() {
  let job;
  try {
    job = await api(`/api/jobs/${state.jobId}`);
  } catch (error) {
    showBanner(`Lost track of the job: ${describe(error)}`);
    stopPolling();
    setBusy(false);
    return;
  }

  showJob(job);
  if (job.state === "running") {
    return;
  }

  stopPolling();
  setBusy(false);
  if (job.state === "succeeded") {
    if (job.kind === "apply") {
      await loadRules();
      showBanner("Applied. The subscription now serves these rules.", true);
    } else {
      setDirty(true);
      showBanner("Dry run passed. Nothing was written or applied.", true);
    }
  } else {
    showBanner(job.error || "The job failed, see the log below.");
  }
}

function stopPolling() {
  if (state.jobTimer) {
    clearInterval(state.jobTimer);
    state.jobTimer = null;
  }
}

function describe(error) {
  if (!error || !error.detail) {
    return "unknown error";
  }
  if (typeof error.detail === "string") {
    return error.detail;
  }
  if (Array.isArray(error.detail.errors)) {
    return error.detail.errors.map((item) => item.message).join("\n");
  }
  return JSON.stringify(error.detail);
}

function applyValidationErrors(error) {
  state.rowErrors.clear();
  const items = (error.detail && error.detail.errors) || [];
  const general = [];
  items.forEach((item) => {
    if (item.index === null || item.index === undefined) {
      general.push(item.message);
      return;
    }
    const messages = state.rowErrors.get(item.index) || [];
    messages.push(item.message);
    state.rowErrors.set(item.index, messages);
  });
  render();
  const rowCount = state.rowErrors.size;
  let summary = "";
  if (rowCount === 1) {
    summary = "1 rule needs fixing.";
  } else if (rowCount > 1) {
    summary = `${rowCount} rules need fixing.`;
  }
  showBanner([summary, ...general].filter(Boolean).join("\n"));
}

async function start(dryRun) {
  setBusy(true);
  showBanner("");
  try {
    const job = await api("/api/apply", {
      method: "POST",
      body: JSON.stringify({
        rules: state.rules,
        base_revision: state.revision,
        dry_run: dryRun,
      }),
    });
    state.jobId = job.id;
    showJob(job);
    state.jobTimer = setInterval(pollJob, 1000);
  } catch (error) {
    setBusy(false);
    if (error.status === 400) {
      applyValidationErrors(error);
    } else {
      showBanner(describe(error));
    }
  }
}

async function init() {
  try {
    state.meta = await api("/api/meta");
    el.meta.innerHTML = "";
    el.meta.append(
      document.createTextNode("editing "),
      Object.assign(document.createElement("code"), { textContent: state.meta.rules_file }),
      document.createTextNode(", applied with "),
      Object.assign(document.createElement("code"), { textContent: state.meta.playbook }),
    );
    await loadRules();
    showBanner("");
  } catch (error) {
    showBanner(describe(error));
    return;
  }

  const current = await api("/api/jobs/current").catch(() => null);
  if (current && current.state === "running") {
    state.jobId = current.id;
    setBusy(true);
    showJob(current);
    state.jobTimer = setInterval(pollJob, 1000);
  }
}

el.add.addEventListener("click", add);
el.reload.addEventListener("click", async () => {
  if (state.dirty && !confirm("Discard the unsaved changes?")) {
    return;
  }
  try {
    await loadRules();
    showBanner("");
  } catch (error) {
    showBanner(describe(error));
  }
});
el.dryRun.addEventListener("click", () => start(true));
el.apply.addEventListener("click", () => start(false));
el.jobToggle.addEventListener("click", () => {
  const hidden = el.jobLog.hasAttribute("hidden");
  el.jobLog.toggleAttribute("hidden", !hidden);
  el.jobToggle.textContent = hidden ? "Hide log" : "Show log";
});

window.addEventListener("beforeunload", (event) => {
  if (state.dirty || state.running) {
    event.preventDefault();
    event.returnValue = "";
  }
});

init();
