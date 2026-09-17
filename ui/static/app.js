"use strict";

const STAGE_LABELS = { generate: "generate", lint: "lint", verify: "verify", approve: "approve", apply: "apply" };

let currentRunId = null;
let currentStages = [];
let eventSource = null;

const form = document.getElementById("run-form");
const runBtn = document.getElementById("run-btn");
const appTypeSelect = document.getElementById("app-type");
const progressPanel = document.getElementById("progress-panel");
const stagesEl = document.getElementById("stages");
const approvePanel = document.getElementById("approve-panel");
const approveBtn = document.getElementById("approve-btn");
const resultBanner = document.getElementById("result-banner");
const retryBtn = document.getElementById("retry-btn");

async function loadAppTypes() {
  const res = await fetch("/api/app-types");
  const data = await res.json();
  appTypeSelect.innerHTML = "";
  for (const t of data.app_types) {
    const opt = document.createElement("option");
    opt.value = t.name;
    const bits = [t.image];
    if (t.needs_install_step) bits.push("install step");
    if (t.has_port) bits.push(`port`);
    opt.textContent = `${t.name} (${bits.join(", ")})`;
    appTypeSelect.appendChild(opt);
  }
}

function requestedStages() {
  const boxes = document.querySelectorAll("#stage-checks input[type=checkbox]");
  return Array.from(boxes)
    .filter((b) => b.checked)
    .map((b) => b.value);
}

function stageRowId(stage) {
  return `stage-${stage}`;
}

function ensureStageRow(stage) {
  let row = document.getElementById(stageRowId(stage));
  if (row) return row;

  row = document.createElement("div");
  row.className = "stage-row";
  row.id = stageRowId(stage);
  row.innerHTML = `
    <div class="stage-head">
      <span class="stage-name">${STAGE_LABELS[stage] || stage}</span>
      <span class="pill" data-role="pill">pending</span>
    </div>
    <div class="stage-body" data-role="body">
      <div data-role="artifact"></div>
      <pre data-role="stdout" hidden></pre>
      <pre class="stderr" data-role="stderr" hidden></pre>
    </div>
  `;
  row.querySelector(".stage-head").addEventListener("click", () => {
    row.querySelector('[data-role="body"]').classList.toggle("open");
  });
  stagesEl.appendChild(row);
  return row;
}

function renderStages(stages) {
  stagesEl.innerHTML = "";
  for (const s of stages) ensureStageRow(s);
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function applyEvent(ev) {
  if (ev.stage === "done") {
    finalizeRun(ev);
    return;
  }

  const row = ensureStageRow(ev.stage);
  const pill = row.querySelector('[data-role="pill"]');
  pill.textContent = ev.status;
  pill.className = `pill ${ev.status}`;

  const stdoutEl = row.querySelector('[data-role="stdout"]');
  const stderrEl = row.querySelector('[data-role="stderr"]');
  const artifactEl = row.querySelector('[data-role="artifact"]');

  if (ev.stdout) {
    stdoutEl.hidden = false;
    stdoutEl.textContent = ev.stdout;
  }
  if (ev.stderr) {
    stderrEl.hidden = false;
    stderrEl.textContent = ev.stderr;
  } else {
    stderrEl.hidden = true;
  }

  if (ev.artifact && ev.stage === "generate") {
    artifactEl.innerHTML = `
      <div class="artifact-box">
        <button class="btn-secondary copy-btn" data-role="copy">Copy</button>
        <pre data-role="artifact-pre"></pre>
      </div>
    `;
    artifactEl.querySelector('[data-role="artifact-pre"]').textContent = ev.artifact;
    artifactEl.querySelector('[data-role="copy"]').addEventListener("click", () => {
      navigator.clipboard.writeText(ev.artifact);
    });
    row.querySelector('[data-role="body"]').classList.add("open");
  }

  if (ev.artifact && ev.stage === "apply") {
    artifactEl.innerHTML = `<p>PR: <a href="${escapeHtml(ev.artifact)}" target="_blank" rel="noopener">${escapeHtml(ev.artifact)}</a></p>`;
  }

  if (ev.status === "failed") {
    row.querySelector('[data-role="body"]').classList.add("open");
  }

  if (ev.stage === "approve" && ev.status === "awaiting_approval") {
    approvePanel.hidden = false;
  }
}

function finalizeRun(ev) {
  resultBanner.classList.remove("ok", "failed");
  resultBanner.classList.add("show", ev.status);
  if (ev.status === "ok") {
    resultBanner.innerHTML = ev.pr_url
      ? `Done. <a href="${escapeHtml(ev.pr_url)}" target="_blank" rel="noopener">Open PR</a>`
      : "Done.";
    retryBtn.hidden = true;
  } else {
    resultBanner.textContent = "Run failed - see the failing stage above for stderr.";
    retryBtn.hidden = false;
  }
  approvePanel.hidden = true;
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
}

function connectStream(runId) {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/stream/${runId}`);
  eventSource.onmessage = (msg) => applyEvent(JSON.parse(msg.data));
  eventSource.onerror = () => {
    // Fall back to polling /api/runs/{run_id} if the stream drops - it
    // returns the same state an SSE replay would have given us.
    eventSource.close();
    eventSource = null;
    pollUntilDone(runId);
  };
}

async function pollUntilDone(runId) {
  const res = await fetch(`/api/runs/${runId}`);
  const run = await res.json();
  for (const ev of run.events) applyEvent(ev);
  if (run.status === "running" || run.status === "awaiting_approval") {
    setTimeout(() => pollUntilDone(runId), 1500);
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  runBtn.disabled = true;
  resultBanner.classList.remove("show", "ok", "failed");
  approvePanel.hidden = true;
  retryBtn.hidden = true;

  const repo = document.getElementById("repo").value.trim();
  const appType = appTypeSelect.value;
  currentStages = requestedStages();

  progressPanel.hidden = false;
  renderStages(currentStages);

  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo_path: repo, app_type: appType, stages: currentStages }),
  });

  if (!res.ok) {
    const err = await res.json();
    resultBanner.classList.add("show", "failed");
    resultBanner.textContent = `Could not start run: ${err.detail || res.statusText}`;
    runBtn.disabled = false;
    return;
  }

  const { run_id } = await res.json();
  currentRunId = run_id;
  connectStream(run_id);
  runBtn.disabled = false;
});

approveBtn.addEventListener("click", async () => {
  approveBtn.disabled = true;
  approvePanel.hidden = true;
  const res = await fetch(`/api/runs/${currentRunId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decided_by: "web-ui" }),
  });
  approveBtn.disabled = false;
  if (!res.ok) {
    const err = await res.json();
    resultBanner.classList.add("show", "failed");
    resultBanner.textContent = `Could not approve: ${err.detail || res.statusText}`;
  }
  // The existing SSE connection stays open across the approval gap and
  // will keep emitting events for approve/apply - no reconnect needed.
});

retryBtn.addEventListener("click", async () => {
  retryBtn.hidden = true;
  resultBanner.classList.remove("show", "ok", "failed");
  const res = await fetch(`/api/runs/${currentRunId}/retry`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json();
    resultBanner.classList.add("show", "failed");
    resultBanner.textContent = `Could not retry: ${err.detail || res.statusText}`;
    return;
  }
  connectStream(currentRunId);
});

loadAppTypes();
