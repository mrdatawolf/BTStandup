const entryInput = document.getElementById("entryInput");
const dateInput = document.getElementById("dateInput");
const initialsInput = document.getElementById("initialsInput");
const addButton = document.getElementById("addButton");
const entriesContainer = document.getElementById("entries");
const statusElement = document.getElementById("status");
const appVersion = document.getElementById("appVersion");
const searchInput = document.getElementById("searchInput");
const dateFromInput = document.getElementById("dateFromInput");
const dateToInput = document.getElementById("dateToInput");
const filterInitialsInput = document.getElementById("filterInitialsInput");
const sortInput = document.getElementById("sortInput");
const deletedInput = document.getElementById("deletedInput");
const compactInput = document.getElementById("compactInput");
const clearFiltersButton = document.getElementById("clearFiltersButton");
const resultCount = document.getElementById("resultCount");

const clientIdKey = "btStandupClientId";
let clientId = localStorage.getItem(clientIdKey);
if (!clientId) {
  clientId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  localStorage.setItem(clientIdKey, clientId);
}

let entries = [];
let draggedIndex = null;
let activeRequests = 0;
let refreshPending = false;
let refreshTimer = null;
let searchTimer = null;
const progressTimers = new Map();

class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

function localToday() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function setStatus(message = "", isError = false) {
  statusElement.textContent = message;
  statusElement.classList.toggle("error", isError);
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Client-ID": clientId,
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(body.error || `Request failed (${response.status})`, response.status, body);
  }
  return response.status === 204 ? null : response.json();
}

function cleanInitials(value) {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 5);
}

function formatDate(value) {
  const [year, month, day] = value.split("-");
  return new Date(year, Number(month) - 1, day).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
}

function formatTimestamp(value) {
  return new Date(value).toLocaleString();
}

function isEditing() {
  return activeRequests > 0 || progressTimers.size > 0 ||
    Boolean(document.querySelector(".edit-form, .notes-panel, .project-panel"));
}

function finishRequest() {
  activeRequests = Math.max(0, activeRequests - 1);
  if (!isEditing() && refreshPending) {
    refreshPending = false;
    loadEntries(true);
  }
}

function requestRefresh() {
  if (isEditing()) {
    refreshPending = true;
  } else {
    refreshPending = false;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => loadEntries(true), 250);
  }
}

function filtersFromPage() {
  return {
    q: searchInput.value.trim(),
    target_date_from: dateFromInput.value,
    target_date_to: dateToInput.value,
    initials: cleanInitials(filterInitialsInput.value),
    sort: sortInput.value,
    deleted: deletedInput.checked ? "true" : "false",
    compact: compactInput.checked ? "true" : "false",
  };
}

function entriesUrl() {
  const values = filtersFromPage();
  const parameters = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value && !(key === "sort" && value === "manual") &&
        !((key === "deleted" || key === "compact") && value === "false")) {
      parameters.set(key, value);
    }
  });
  const query = parameters.toString();
  history.replaceState(null, "", query ? `?${query}` : location.pathname);
  return `/api/entries${query ? `?${query}` : ""}`;
}

function loadFiltersFromUrl() {
  const parameters = new URLSearchParams(location.search);
  searchInput.value = parameters.get("q") || "";
  dateFromInput.value = parameters.get("target_date_from") || "";
  dateToInput.value = parameters.get("target_date_to") || "";
  filterInitialsInput.value = cleanInitials(parameters.get("initials") || "");
  sortInput.value = parameters.get("sort") || "manual";
  if (![...sortInput.options].some(option => option.value === sortInput.value)) {
    sortInput.value = "manual";
  }
  deletedInput.checked = parameters.get("deleted") === "true";
  compactInput.checked = parameters.get("compact") === "true";
}

function manualOrderingEnabled() {
  const filters = filtersFromPage();
  return filters.sort === "manual" && !filters.q && !filters.target_date_from &&
    !filters.target_date_to && !filters.initials && filters.deleted === "false";
}

async function saveEntry(entry, updates) {
  activeRequests++;
  try {
    const saved = await apiRequest(`/api/entries/${entry.id}`, {
      method: "PATCH",
      body: JSON.stringify({ ...updates, revision: entry.revision }),
    });
    Object.assign(entry, saved);
    return saved;
  } catch (error) {
    if (error.status === 409 && error.body.current) {
      Object.assign(entry, error.body.current);
    }
    throw error;
  } finally {
    finishRequest();
  }
}

async function showProjectPicker(item, entry) {
  item.querySelector(".project-panel")?.remove();
  const panel = document.createElement("div");
  panel.className = "project-panel";
  panel.textContent = "Loading BiztechProjects projects...";
  item.appendChild(panel);
  try {
    const projects = await apiRequest("/api/integrations/biztech-projects/projects");
    panel.innerHTML = `
      <label class="notes-label">Link to a BiztechProjects project</label>
      <input class="project-search" type="text" placeholder="Search projects...">
      <select class="project-select" size="6" aria-label="BiztechProjects project"></select>
      <div class="notes-actions">
        <button class="save-button link-selected-project">Link project</button>
        <button class="cancel-button cancel-project-link">Cancel</button>
      </div>`;
    const search = panel.querySelector(".project-search");
    const select = panel.querySelector(".project-select");
    const linkButton = panel.querySelector(".link-selected-project");
    const populate = () => {
      const query = search.value.trim().toLowerCase();
      const matches = projects.filter(project =>
        `${project.title} ${project.status || ""}`.toLowerCase().includes(query)
      );
      select.replaceChildren(...matches.map(project => {
        const option = document.createElement("option");
        option.value = project.id;
        option.textContent = `${project.title} — ${project.progress}%${
          project.status ? ` (${project.status})` : ""
        }`;
        return option;
      }));
      if (select.options.length) select.selectedIndex = 0;
      linkButton.disabled = !select.options.length;
    };
    populate();
    search.addEventListener("input", populate);
    panel.querySelector(".cancel-project-link").addEventListener("click", () => {
      panel.remove();
      if (refreshPending) requestRefresh();
    });
    linkButton.addEventListener("click", async () => {
      if (!select.value) return;
      linkButton.disabled = true;
      activeRequests++;
      setStatus("Linking project...");
      try {
        const linked = await apiRequest(`/api/entries/${entry.id}/project-link`, {
          method: "POST",
          body: JSON.stringify({ project_id: Number(select.value), revision: entry.revision }),
        });
        Object.assign(entry, linked);
        renderEntries();
        setStatus();
      } catch (error) {
        setStatus(error.message, true);
        linkButton.disabled = false;
      } finally {
        finishRequest();
      }
    });
    search.focus();
  } catch (error) {
    panel.textContent = `Could not load projects: ${error.message}`;
    setStatus(error.message, true);
  }
}

function renderHistory(panel, events) {
  panel.innerHTML = '<strong class="history-title">Change history</strong>';
  if (!events.length) {
    panel.insertAdjacentHTML("beforeend", '<p class="history-empty">No history recorded.</p>');
    return;
  }
  const list = document.createElement("ol");
  list.className = "history-list";
  events.forEach(event => {
    const item = document.createElement("li");
    const label = event.event_type.replaceAll("_", " ");
    const fields = event.changed_fields.length ? ` · ${event.changed_fields.join(", ")}` : "";
    item.textContent = `${formatTimestamp(event.occurred_at)} — ${label}${fields}`;
    list.appendChild(item);
  });
  panel.appendChild(list);
}

async function toggleHistory(item, entry) {
  const existing = item.querySelector(".history-panel");
  if (existing) return existing.remove();
  const panel = document.createElement("div");
  panel.className = "history-panel";
  panel.textContent = "Loading history...";
  item.appendChild(panel);
  try {
    renderHistory(panel, await apiRequest(`/api/entries/${entry.id}/history`));
  } catch (error) {
    panel.textContent = `Could not load history: ${error.message}`;
  }
}

function setNotesPanel(item, entry, isOpen) {
  const oldPanel = item.querySelector(".notes-panel");
  if (oldPanel) oldPanel.remove();
  const button = item.querySelector(".notes-button");
  button.textContent = isOpen
    ? "Close notes"
    : (entry.notes ? "Notes" : (entry.deleted_at ? "No notes" : "Add notes"));
  if (!isOpen) {
    if (refreshPending) requestRefresh();
    return;
  }

  const panel = document.createElement("div");
  panel.className = "notes-panel";
  panel.innerHTML = `
    <label class="notes-label" for="notes-${entry.id}">Notes</label>
    <textarea id="notes-${entry.id}" maxlength="10000" placeholder="Write notes for this entry..."></textarea>
    <div class="notes-actions">
      <button class="save-button">Save notes</button>
      <button class="cancel-button">Close</button>
    </div>`;
  const textarea = panel.querySelector("textarea");
  if (entry.deleted_at) textarea.disabled = true;
  textarea.value = entry.notes;
  if (entry.deleted_at) panel.querySelector(".save-button").remove();
  panel.querySelector(".save-button")?.addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    setStatus("Saving notes...");
    try {
      await saveEntry(entry, { notes: textarea.value });
      setNotesPanel(item, entry, false);
      setStatus();
    } catch (error) {
      setStatus(error.message, true);
      event.currentTarget.disabled = false;
    }
  });
  panel.querySelector(".cancel-button").addEventListener("click", () =>
    setNotesPanel(item, entry, false)
  );
  item.appendChild(panel);
  textarea.focus();
}

async function saveOrder() {
  activeRequests++;
  setStatus("Saving order...");
  try {
    entries = await apiRequest("/api/entries/order", {
      method: "PUT",
      body: JSON.stringify({ entries: entries.map(({ id, revision }) => ({ id, revision })) }),
    });
    renderEntries();
    setStatus();
  } catch (error) {
    setStatus(error.message, true);
    await loadEntries(true);
  } finally {
    finishRequest();
  }
}

function renderEntries() {
  entriesContainer.innerHTML = "";
  entriesContainer.classList.toggle("compact-view", compactInput.checked);
  resultCount.textContent = `${entries.length} ${entries.length === 1 ? "entry" : "entries"}`;
  if (!entries.length) {
    const filtered = location.search.length > 0;
    entriesContainer.innerHTML = `<p class="empty-message">${
      filtered ? "No entries match these filters." : "No entries yet. Add one above."
    }</p>`;
    return;
  }

  const canReorder = manualOrderingEnabled();
  entries.forEach((entry, index) => {
    const item = document.createElement("article");
    item.className = `entry${entry.deleted_at ? " deleted-entry" : ""}`;
    item.dataset.entryId = entry.id;
    item.innerHTML = `
      <div class="entry-header">
        <div class="entry-top-left">
          ${canReorder ? '<span class="drag-handle" draggable="true" title="Drag to reorder">⠿</span>' : ""}
          <div class="entry-title" title="Click to edit">
            <span class="entry-name"></span>
            <span class="entry-date"></span>
          </div>
        </div>
        <div class="entry-actions">
          <span class="initials-badge ${entry.initials ? "" : "empty"}"></span>
          ${!entry.deleted_at && !entry.external_system
            ? '<button class="link-project-button">Link project</button>' : ""}
          ${entry.external_system
            ? '<a class="open-project-link" target="_blank" rel="noopener noreferrer">Open project ↗</a>' : ""}
          ${!entry.deleted_at && entry.external_system
            ? '<button class="refresh-project-button">Refresh</button><button class="unlink-project-button">Unlink</button>' : ""}
          <button class="notes-button">${entry.notes ? "Notes" : (entry.deleted_at ? "No notes" : "Add notes")}</button>
          <button class="history-button">History</button>
          ${entry.deleted_at
            ? '<button class="restore-button">Restore</button>'
            : '<button class="delete-button">Delete</button>'}
        </div>
      </div>
      <div class="progress-row">
        ${entry.external_system
          ? '<span class="managed-progress">Managed by BiztechProjects</span>'
          : `<input class="progress-slider" type="range" min="0" max="100" value="${entry.progress}" ${entry.deleted_at ? "disabled" : ""}>`}
        <span class="percent">${entry.progress}%</span>
      </div>
      ${entry.external_system ? '<div class="project-meta"><span class="project-title"></span><span class="project-status"></span><span class="project-synced"></span></div>' : ""}
      <div class="bar-background"><div class="bar-fill" style="width: ${entry.progress}%"></div></div>`;

    item.querySelector(".entry-name").textContent = entry.name;
    item.querySelector(".entry-date").textContent = `Target: ${formatDate(entry.target_date)}`;
    item.querySelector(".initials-badge").textContent = entry.initials || "Initials";
    item.querySelector(".history-button").addEventListener("click", () => toggleHistory(item, entry));
    if (entry.external_system) {
      item.querySelector(".project-title").textContent = entry.external_project_title || "Linked project";
      item.querySelector(".project-status").textContent = entry.external_status || "Status unavailable";
      item.querySelector(".project-synced").textContent = entry.external_synced_at
        ? `Updated ${formatTimestamp(entry.external_synced_at)}` : "Not yet synchronized";
      const openLink = item.querySelector(".open-project-link");
      openLink.href = entry.external_project_url;
    }

    const dragHandle = item.querySelector(".drag-handle");
    if (dragHandle) {
      dragHandle.addEventListener("dragstart", event => {
        draggedIndex = index;
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(index));
        setTimeout(() => item.classList.add("dragging"), 0);
      });
      dragHandle.addEventListener("dragend", () => {
        draggedIndex = null;
        document.querySelectorAll(".entry").forEach(element =>
          element.classList.remove("dragging", "drag-over")
        );
      });
      item.addEventListener("dragover", event => {
        if (draggedIndex === null) return;
        event.preventDefault();
        if (draggedIndex !== index) item.classList.add("drag-over");
      });
      item.addEventListener("dragleave", () => item.classList.remove("drag-over"));
      item.addEventListener("drop", event => {
        if (draggedIndex === null) return;
        event.preventDefault();
        if (draggedIndex === index) return;
        const moved = entries.splice(draggedIndex, 1)[0];
        let targetIndex = index - (draggedIndex < index ? 1 : 0);
        const bounds = item.getBoundingClientRect();
        if (event.clientY > bounds.top + bounds.height / 2) targetIndex++;
        entries.splice(targetIndex, 0, moved);
        draggedIndex = null;
        renderEntries();
        saveOrder();
      });
    }

    const slider = item.querySelector(".progress-slider");
    const percent = item.querySelector(".percent");
    const fill = item.querySelector(".bar-fill");
    slider?.addEventListener("input", () => {
      entry.progress = Number(slider.value);
      percent.textContent = `${entry.progress}%`;
      fill.style.width = `${entry.progress}%`;
      setStatus("Saving...");
      clearTimeout(progressTimers.get(entry.id));
      progressTimers.set(entry.id, setTimeout(async () => {
        try {
          await saveEntry(entry, { progress: entry.progress });
          setStatus();
        } catch (error) {
          setStatus(error.message, true);
        } finally {
          progressTimers.delete(entry.id);
          if (refreshPending) requestRefresh();
        }
      }, 350));
    });

    item.querySelector(".notes-button").addEventListener("click", () =>
      setNotesPanel(item, entry, !item.querySelector(".notes-panel"))
    );

    if (entry.deleted_at) {
      item.querySelector(".restore-button").addEventListener("click", async event => {
        event.currentTarget.disabled = true;
        activeRequests++;
        try {
          await apiRequest(`/api/entries/${entry.id}/restore`, {
            method: "POST", body: JSON.stringify({ revision: entry.revision }),
          });
          await loadEntries(true);
        } catch (error) {
          setStatus(error.message, true);
        } finally {
          finishRequest();
        }
      });
    } else {
      item.querySelector(".link-project-button")?.addEventListener("click", () =>
        showProjectPicker(item, entry)
      );

      item.querySelector(".refresh-project-button")?.addEventListener("click", async event => {
        event.currentTarget.disabled = true;
        activeRequests++;
        setStatus("Refreshing project progress...");
        try {
          const refreshed = await apiRequest(`/api/entries/${entry.id}/project-refresh`, {
            method: "POST", body: JSON.stringify({ revision: entry.revision }),
          });
          Object.assign(entry, refreshed);
          renderEntries();
          setStatus();
        } catch (error) {
          setStatus(error.message, true);
          event.currentTarget.disabled = false;
        } finally {
          finishRequest();
        }
      });

      item.querySelector(".unlink-project-button")?.addEventListener("click", async event => {
        if (!confirm("Unlink this project? The latest project percentage will become editable.")) return;
        event.currentTarget.disabled = true;
        activeRequests++;
        setStatus("Unlinking project...");
        try {
          const unlinked = await apiRequest(`/api/entries/${entry.id}/project-link`, {
            method: "DELETE", body: JSON.stringify({ revision: entry.revision }),
          });
          Object.assign(entry, unlinked);
          renderEntries();
          setStatus();
        } catch (error) {
          setStatus(error.message, true);
          event.currentTarget.disabled = false;
        } finally {
          finishRequest();
        }
      });

      item.querySelector(".delete-button").addEventListener("click", async event => {
        event.currentTarget.disabled = true;
        activeRequests++;
        try {
          await apiRequest(`/api/entries/${entry.id}`, {
            method: "DELETE", body: JSON.stringify({ revision: entry.revision }),
          });
          entries = entries.filter(candidate => candidate.id !== entry.id);
          renderEntries();
          setStatus();
        } catch (error) {
          setStatus(error.message, true);
          event.currentTarget.disabled = false;
        } finally {
          finishRequest();
        }
      });

      item.querySelector(".initials-badge").addEventListener("click", async () => {
        const value = prompt("Enter initials (up to 5 letters or numbers):", entry.initials);
        if (value === null) return;
        try {
          await saveEntry(entry, { initials: cleanInitials(value) });
          renderEntries();
          if (refreshPending) requestRefresh();
        } catch (error) {
          setStatus(error.message, true);
        }
      });

      item.querySelector(".entry-title").addEventListener("click", () => {
        item.innerHTML = `
          <div class="edit-form">
            <input class="edit-name" type="text" maxlength="500">
            <input class="edit-date" type="date" value="${entry.target_date}" required>
            <input class="edit-initials" type="text" maxlength="5" value="${entry.initials}">
            <button class="save-button">Save</button>
            <button class="cancel-button">Cancel</button>
          </div>`;
        const nameField = item.querySelector(".edit-name");
        nameField.value = entry.name;
        nameField.focus();
        const saveEdit = async () => {
          const name = nameField.value.trim();
          const targetDate = item.querySelector(".edit-date").value;
          if (!name || !targetDate) return;
          try {
            await saveEntry(entry, {
              name,
              target_date: targetDate,
              initials: cleanInitials(item.querySelector(".edit-initials").value),
            });
            renderEntries();
            if (refreshPending) requestRefresh();
          } catch (error) {
            setStatus(error.message, true);
          }
        };
        item.querySelector(".save-button").addEventListener("click", saveEdit);
        item.querySelector(".cancel-button").addEventListener("click", () => {
          renderEntries();
          if (refreshPending) requestRefresh();
        });
        item.querySelectorAll("input").forEach(field => field.addEventListener("keydown", event => {
          if (event.key === "Enter") saveEdit();
          if (event.key === "Escape") item.querySelector(".cancel-button").click();
        }));
      });
    }
    entriesContainer.appendChild(item);
  });
}

async function loadEntries(silent = false) {
  if (!silent) setStatus("Loading...");
  try {
    entries = await apiRequest(entriesUrl());
    renderEntries();
    if (!silent) setStatus();
  } catch (error) {
    setStatus(`Could not load entries: ${error.message}`, true);
  }
}

async function addEntry() {
  const name = entryInput.value.trim();
  if (!name) return entryInput.focus();
  addButton.disabled = true;
  activeRequests++;
  try {
    await apiRequest("/api/entries", {
      method: "POST",
      body: JSON.stringify({
        name,
        target_date: dateInput.value || localToday(),
        initials: cleanInitials(initialsInput.value),
      }),
    });
    entryInput.value = "";
    dateInput.value = localToday();
    initialsInput.value = "";
    await loadEntries(true);
    entryInput.focus();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    addButton.disabled = false;
    finishRequest();
  }
}

function filtersChanged() {
  loadEntries(true);
}

loadFiltersFromUrl();
dateInput.value = localToday();
initialsInput.addEventListener("input", () => initialsInput.value = cleanInitials(initialsInput.value));
filterInitialsInput.addEventListener("input", () => {
  filterInitialsInput.value = cleanInitials(filterInitialsInput.value);
  filtersChanged();
});
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(filtersChanged, 300);
});
[dateFromInput, dateToInput, sortInput, deletedInput, compactInput].forEach(element =>
  element.addEventListener("change", filtersChanged)
);
clearFiltersButton.addEventListener("click", () => {
  searchInput.value = "";
  dateFromInput.value = "";
  dateToInput.value = "";
  filterInitialsInput.value = "";
  sortInput.value = "manual";
  deletedInput.checked = false;
  compactInput.checked = false;
  filtersChanged();
});
addButton.addEventListener("click", addEntry);
entryInput.addEventListener("keydown", event => {
  if (event.key === "Enter") addEntry();
});

apiRequest("/api/version")
  .then(result => appVersion.textContent = result.version || "unknown")
  .catch(() => appVersion.textContent = "unknown");
loadEntries();

const eventSource = new EventSource("/api/events");
eventSource.addEventListener("entry_changed", requestRefresh);
eventSource.onerror = () => setStatus("Live updates reconnecting...", true);
eventSource.onopen = () => {
  if (statusElement.textContent === "Live updates reconnecting...") setStatus();
};
setInterval(() => requestRefresh(), 5 * 60 * 1000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") requestRefresh();
});
