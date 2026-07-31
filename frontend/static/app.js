/* Smart Upload Engine dashboard.
 * Plain JS + polling (no build step needed) — kept deliberately simple
 * since this is a single-user personal dashboard, not a large SPA. */

const REFRESH_MS = 3000;
const DISK_REFRESH_MS = 5000;

function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined) return "?";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

function fmtSpeed(bps) {
  if (!bps) return "0 B/s";
  return `${fmtBytes(bps)}/s`;
}

function fmtEta(seconds) {
  if (seconds === null || seconds === undefined) return "--";
  seconds = Math.max(0, Math.round(seconds));
  const m = Math.floor(seconds / 60), s = seconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

async function apiGet(path) {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

async function apiPost(path, body) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}));
    throw new Error(detail.detail || resp.statusText);
  }
  return resp.json();
}

async function apiDelete(path) {
  const resp = await fetch(path, { method: "DELETE" });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

function taskCard(task) {
  const pct = task.total_bytes
    ? Math.round(100 * ((task.status === "uploading" ? task.bytes_uploaded : task.bytes_downloaded) / task.total_bytes))
    : (task.status === "completed" ? 100 : 0);

  const showProgress = ["downloading", "uploading"].includes(task.status);
  const showActions = ["waiting", "downloading", "uploading", "paused"].includes(task.status);

  const div = document.createElement("div");
  div.className = "card";
  div.innerHTML = `
    <div class="card-top">
      <div>
        <div class="card-name">${task.filename || task.url}</div>
        <div class="card-sub">${{google_drive:"Google Drive",gofile:"GoFile",buzzheavier:"BuzzHeavier"}[task.destination] || task.destination} · ${task.method ? task.method : "auto"}</div>
      </div>
      <span class="badge ${task.status}">${task.status}</span>
    </div>
    ${showProgress ? `
      <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
      <div class="stats-row">
        <span>${pct}%</span>
        <span>${fmtBytes(task.status === "uploading" ? task.bytes_uploaded : task.bytes_downloaded)} / ${fmtBytes(task.total_bytes)}</span>
        <span>↓ ${fmtSpeed(task.download_speed_bps)}</span>
        <span>↑ ${fmtSpeed(task.upload_speed_bps)}</span>
        <span>ETA ${fmtEta(task.eta_seconds)}</span>
      </div>` : ""}
    ${task.status === "completed" && task.remote_link ? `<a class="card-sub" href="${task.remote_link}" target="_blank" rel="noopener">Open uploaded file →</a>` : ""}
    ${task.error ? `<div class="error-text">${task.error}</div>` : ""}
    ${showActions ? `<div class="card-actions"><button data-action="cancel" data-id="${task.id}">Cancel</button></div>` : `<div class="card-actions"><button data-action="delete" data-id="${task.id}">Remove</button></div>`}
  `;
  return div;
}

function renderList(panelId, tasks, emptyMessage) {
  const panel = document.getElementById(panelId);
  panel.innerHTML = "";
  if (tasks.length === 0) {
    panel.innerHTML = `<div class="empty-state">${emptyMessage}</div>`;
    return;
  }
  for (const t of tasks) panel.appendChild(taskCard(t));
}

async function refreshQueue() {
  try {
    const all = await apiGet("api/tasks");
    const queue = all.filter(t => ["waiting", "downloading", "uploading", "paused"].includes(t.status));
    const completed = all.filter(t => t.status === "completed");
    const failed = all.filter(t => ["failed", "cancelled"].includes(t.status));
    renderList("panel-queue", queue, "Nothing in the queue right now.");
    renderList("panel-completed", completed, "No completed uploads yet.");
    renderList("panel-failed", failed, "No failed or cancelled uploads.");
  } catch (e) {
    console.error("Failed to refresh queue:", e);
  }
}

async function refreshDisk() {
  try {
    const disk = await apiGet("api/disk");
    document.getElementById("disk-bar-fill").style.width = `${disk.used_percent}%`;
    document.getElementById("disk-text").textContent =
      `${disk.free_gb} GB free of ${disk.total_gb} GB`;
  } catch (e) {
    console.error("Failed to refresh disk usage:", e);
  }
}

/* --- Multi-row "Add downloads" form ---
 * Each row is its own URL + destination + optional filename. You can add
 * as many rows as you want with "+ Add URL" and remove any single row
 * with its ×. Submitting queues every filled-in row individually. */

const urlRowsEl = document.getElementById("url-rows");
const rowTemplate = document.getElementById("url-row-template");

function addUrlRow(focus = true) {
  const node = rowTemplate.content.firstElementChild.cloneNode(true);
  urlRowsEl.appendChild(node);
  updateRemoveButtons();
  if (focus) node.querySelector(".row-url").focus();
  return node;
}

function updateRemoveButtons() {
  const rows = urlRowsEl.querySelectorAll(".url-row");
  // Keep at least one row always present; disable its remove button.
  rows.forEach((row, i) => {
    row.querySelector(".remove-row-btn").disabled = rows.length === 1;
  });
}

document.getElementById("add-row-btn").addEventListener("click", () => addUrlRow());

urlRowsEl.addEventListener("click", (evt) => {
  const btn = evt.target.closest(".remove-row-btn");
  if (!btn || btn.disabled) return;
  const row = btn.closest(".url-row");
  row.classList.add("row-removing");
  row.addEventListener("animationend", () => {
    row.remove();
    updateRemoveButtons();
  }, { once: true });
});

// Pressing Enter in a URL field adds a new row instead of submitting,
// so you can rapid-fire paste one link after another.
urlRowsEl.addEventListener("keydown", (evt) => {
  if (evt.key === "Enter" && evt.target.classList.contains("row-url")) {
    evt.preventDefault();
    const rows = Array.from(urlRowsEl.querySelectorAll(".url-row"));
    const isLast = rows[rows.length - 1].contains(evt.target);
    if (isLast && evt.target.value.trim()) {
      addUrlRow();
    }
  }
});

addUrlRow(false); // start with exactly one empty row

document.getElementById("submit-form").addEventListener("submit", async (evt) => {
  evt.preventDefault();
  const resultEl = document.getElementById("submit-result");
  const submitBtn = document.getElementById("queue-all-btn");

  const rows = Array.from(urlRowsEl.querySelectorAll(".url-row"));
  const jobs = rows
    .map(row => ({
      url: row.querySelector(".row-url").value.trim(),
      destination: row.querySelector(".row-destination").value,
      filename_override: row.querySelector(".row-filename").value.trim() || null,
      row,
    }))
    .filter(j => j.url);

  if (jobs.length === 0) {
    resultEl.textContent = "Enter at least one URL first.";
    return;
  }

  submitBtn.disabled = true;
  let queued = 0;
  const errors = [];

  for (const job of jobs) {
    try {
      await apiPost("api/tasks", {
        url: job.url,
        destination: job.destination,
        filename_override: job.filename_override,
      });
      queued++;
      job.row.remove();
    } catch (e) {
      errors.push(`${job.url}: ${e.message}`);
    }
  }

  updateRemoveButtons();
  if (urlRowsEl.querySelectorAll(".url-row").length === 0) addUrlRow(false);

  resultEl.textContent = errors.length
    ? `Queued ${queued} of ${jobs.length}. Failed: ${errors.join("; ")}`
    : `Queued ${queued} download${queued === 1 ? "" : "s"}.`;

  submitBtn.disabled = false;
  await refreshQueue();
});

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`panel-${tab.dataset.tab}`).classList.add("active");
  });
});

document.addEventListener("click", async (evt) => {
  const btn = evt.target.closest("button[data-action]");
  if (!btn) return;
  const { action, id } = btn.dataset;
  try {
    if (action === "cancel") await apiPost(`/api/tasks/${id}/cancel`);
    if (action === "delete") await apiDelete(`/api/tasks/${id}`);
    await refreshQueue();
  } catch (e) {
    alert(`Action failed: ${e.message}`);
  }
});

document.getElementById("cancel-all-btn").addEventListener("click", async () => {
  if (!confirm("Cancel every waiting/downloading/uploading task? This can't be undone.")) return;
  try {
    const result = await apiPost("api/tasks/cancel-all");
    await refreshQueue();
    alert(`Cancelled ${result.cancelled} task(s).`);
  } catch (e) {
    alert(`Cancel all failed: ${e.message}`);
  }
});

/* --- Download mode: Manual vs Auto from JSON ---
 * Manual is the default on every page load. Switching to "Auto from
 * JSON" only shows the panel -- it does NOT start anything by itself.
 * If .env already has REMOTE_QUEUE_URL set, Start uses that directly
 * (no re-typing it). If .env has nothing configured, a URL field shows
 * up so you can type one in just this once. */

const REMOTE_QUEUE_REFRESH_MS = 5000;
let remoteQueueDefaultUrl = null;
let remoteQueueDefaultInterval = 60;

function setMode(mode) {
  document.getElementById("mode-manual-btn").classList.toggle("active", mode === "manual");
  document.getElementById("mode-auto-btn").classList.toggle("active", mode === "auto");
  document.getElementById("mode-manual-hint").classList.toggle("hidden", mode === "auto");
  document.getElementById("mode-auto-panel").classList.toggle("hidden", mode !== "auto");
}

document.getElementById("mode-manual-btn").addEventListener("click", () => setMode("manual"));
document.getElementById("mode-auto-btn").addEventListener("click", () => setMode("auto"));

function renderRemoteQueueStatus(status) {
  const el = document.getElementById("remote-queue-status");
  el.classList.remove("status-on", "status-off", "status-error");

  if (status.last_error) {
    el.textContent = `Status: error — ${status.last_error}`;
    el.classList.add("status-error");
    return;
  }

  if (status.enabled) {
    const last = status.last_sync_at
      ? `${Math.max(0, Math.round(Date.now() / 1000 - status.last_sync_at))}s ago`
      : "not yet";
    el.textContent = `Status: Running — checking ${status.url} every ${status.interval_seconds}s ` +
      `(last check: ${last}, ${status.total_added} queued so far)`;
    el.classList.add("status-on");
  } else {
    el.textContent = "Status: off — nothing will auto-download until you press Start.";
    el.classList.add("status-off");
  }
}

async function refreshRemoteQueueStatus() {
  try {
    const status = await apiGet("api/remote-queue");
    renderRemoteQueueStatus(status);

    remoteQueueDefaultUrl = status.default_url;
    remoteQueueDefaultInterval = status.default_interval_seconds || 60;

    const configuredView = document.getElementById("auto-configured-view");
    const unconfiguredView = document.getElementById("auto-unconfigured-view");

    if (remoteQueueDefaultUrl) {
      // .env already has a URL -- don't make the user type it again.
      configuredView.classList.remove("hidden");
      unconfiguredView.classList.add("hidden");
      document.getElementById("auto-configured-url").textContent = remoteQueueDefaultUrl;
      document.getElementById("auto-configured-interval").textContent = remoteQueueDefaultInterval;
    } else {
      configuredView.classList.add("hidden");
      unconfiguredView.classList.remove("hidden");
      const urlInput = document.getElementById("remote-queue-url");
      if (!urlInput.value) urlInput.value = "";
      document.getElementById("remote-queue-interval").value =
        document.getElementById("remote-queue-interval").value || remoteQueueDefaultInterval;
    }
  } catch (e) {
    console.error("Failed to refresh remote queue status:", e);
  }
}

document.getElementById("remote-queue-start-btn").addEventListener("click", async () => {
  // Prefer the .env-configured URL if there is one; otherwise use
  // whatever the user typed into the fallback field.
  const url = remoteQueueDefaultUrl || document.getElementById("remote-queue-url").value.trim();
  const interval_seconds = remoteQueueDefaultUrl
    ? remoteQueueDefaultInterval
    : (parseInt(document.getElementById("remote-queue-interval").value, 10) || 60);

  if (!url) {
    alert("Enter a JSON URL first.");
    return;
  }

  try {
    const status = await apiPost("api/remote-queue/start", { url, interval_seconds });
    renderRemoteQueueStatus(status);
  } catch (e) {
    alert(`Could not start auto mode: ${e.message}`);
  }
});

document.getElementById("remote-queue-stop-btn").addEventListener("click", async () => {
  try {
    const status = await apiPost("api/remote-queue/stop");
    renderRemoteQueueStatus(status);
  } catch (e) {
    alert(`Could not stop auto mode: ${e.message}`);
  }
});

document.getElementById("remote-queue-sync-btn").addEventListener("click", async () => {
  try {
    const result = await apiPost("api/remote-queue/sync-now");
    await refreshRemoteQueueStatus();
    await refreshQueue();
    if (result.error) alert(`Sync failed: ${result.error}`);
  } catch (e) {
    alert(`Sync failed: ${e.message}`);
  }
});

refreshQueue();
refreshDisk();
refreshRemoteQueueStatus();
setInterval(refreshQueue, REFRESH_MS);
setInterval(refreshDisk, DISK_REFRESH_MS);
setInterval(refreshRemoteQueueStatus, REMOTE_QUEUE_REFRESH_MS);
