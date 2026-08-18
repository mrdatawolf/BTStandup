const entryInput = document.getElementById("entryInput");
const dateInput = document.getElementById("dateInput");
const initialsInput = document.getElementById("initialsInput");
const addButton = document.getElementById("addButton");
const entriesContainer = document.getElementById("entries");
const statusElement = document.getElementById("status");
const appVersion = document.getElementById("appVersion");

let entries = [];
let draggedIndex = null;
const progressTimers = new Map();

function setStatus(message = "", isError = false) {
  statusElement.textContent = message;
  statusElement.classList.toggle("error", isError);
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function cleanInitials(value) {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 5);
}

function formatDate(dateValue) {
  if (!dateValue) return "No date set";
  const [year, month, day] = dateValue.split("-");
  return new Date(year, Number(month) - 1, day).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
}

async function saveEntry(entry, updates) {
  const saved = await apiRequest(`/api/entries/${entry.id}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
  Object.assign(entry, saved);
  return saved;
}

function setNotesPanel(item, entry, isOpen) {
  const oldPanel = item.querySelector(".notes-panel");
  if (oldPanel) oldPanel.remove();

  const notesButton = item.querySelector(".notes-button");
  notesButton.textContent = isOpen ? "Close notes" : (entry.notes ? "Notes" : "Add notes");
  if (!isOpen) return;

  const panel = document.createElement("div");
  panel.className = "notes-panel";
  panel.innerHTML = `
    <label class="notes-label" for="notes-${entry.id}">Notes</label>
    <textarea id="notes-${entry.id}" maxlength="10000" placeholder="Write notes for this entry..."></textarea>
    <div class="notes-actions">
      <button class="save-button">Save notes</button>
      <button class="cancel-button">Close</button>
    </div>
  `;

  const textarea = panel.querySelector("textarea");
  const saveButton = panel.querySelector(".save-button");
  textarea.value = entry.notes;
  saveButton.addEventListener("click", async () => {
    saveButton.disabled = true;
    setStatus("Saving notes...");
    try {
      await saveEntry(entry, { notes: textarea.value });
      setNotesPanel(item, entry, false);
      setStatus();
    } catch (error) {
      setStatus(`Could not save notes: ${error.message}`, true);
      saveButton.disabled = false;
    }
  });
  panel.querySelector(".cancel-button").addEventListener("click", () =>
    setNotesPanel(item, entry, false)
  );
  item.appendChild(panel);
  textarea.focus();
}

async function saveOrder() {
  setStatus("Saving order...");
  try {
    await apiRequest("/api/entries/order", {
      method: "PUT",
      body: JSON.stringify({ entry_ids: entries.map(entry => entry.id) }),
    });
    setStatus();
  } catch (error) {
    setStatus(`Could not save order: ${error.message}`, true);
    await loadEntries();
  }
}

function renderEntries() {
  entriesContainer.innerHTML = "";
  if (entries.length === 0) {
    entriesContainer.innerHTML = '<p class="empty-message">No entries yet. Add one above.</p>';
    return;
  }

  entries.forEach((entry, index) => {
    const item = document.createElement("article");
    item.className = "entry";
    item.innerHTML = `
      <div class="entry-header">
        <div class="entry-top-left">
          <span class="drag-handle" draggable="true" title="Drag to reorder" aria-label="Drag to reorder">⠿</span>
          <div class="entry-title" title="Click to edit">
            <span class="entry-name"></span>
            <span class="entry-date"></span>
          </div>
        </div>
        <div class="entry-actions">
          <span class="initials-badge ${entry.initials ? "" : "empty"}" title="Click to edit initials"></span>
          <button class="notes-button">${entry.notes ? "Notes" : "Add notes"}</button>
          <button class="delete-button">Delete</button>
        </div>
      </div>
      <div class="progress-row">
        <input class="progress-slider" type="range" min="0" max="100" value="${entry.progress}">
        <span class="percent">${entry.progress}%</span>
      </div>
      <div class="bar-background"><div class="bar-fill" style="width: ${entry.progress}%"></div></div>
    `;

    item.querySelector(".entry-name").textContent = entry.name;
    item.querySelector(".entry-date").textContent = formatDate(entry.date);
    item.querySelector(".initials-badge").textContent = entry.initials || "Initials";

    const dragHandle = item.querySelector(".drag-handle");
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
      item.classList.remove("drag-over");
      if (draggedIndex === index) return;

      const movedEntry = entries.splice(draggedIndex, 1)[0];
      let targetIndex = index;
      if (draggedIndex < index) targetIndex--;
      const bounds = item.getBoundingClientRect();
      if (event.clientY > bounds.top + bounds.height / 2) targetIndex++;
      entries.splice(targetIndex, 0, movedEntry);
      draggedIndex = null;
      renderEntries();
      saveOrder();
    });

    const slider = item.querySelector(".progress-slider");
    const percent = item.querySelector(".percent");
    const fill = item.querySelector(".bar-fill");
    slider.addEventListener("input", () => {
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
          setStatus(`Could not save progress: ${error.message}`, true);
        } finally {
          progressTimers.delete(entry.id);
        }
      }, 350));
    });

    item.querySelector(".delete-button").addEventListener("click", async event => {
      event.currentTarget.disabled = true;
      setStatus("Deleting...");
      try {
        await apiRequest(`/api/entries/${entry.id}`, { method: "DELETE" });
        entries = entries.filter(candidate => candidate.id !== entry.id);
        renderEntries();
        setStatus();
      } catch (error) {
        setStatus(`Could not delete: ${error.message}`, true);
        event.currentTarget.disabled = false;
      }
    });
    item.querySelector(".notes-button").addEventListener("click", () =>
      setNotesPanel(item, entry, !item.querySelector(".notes-panel"))
    );
    item.querySelector(".initials-badge").addEventListener("click", async () => {
      const value = prompt("Enter initials (up to 5 letters or numbers):", entry.initials);
      if (value === null) return;
      try {
        await saveEntry(entry, { initials: cleanInitials(value) });
        renderEntries();
        setStatus();
      } catch (error) {
        setStatus(`Could not save initials: ${error.message}`, true);
      }
    });

    item.querySelector(".entry-title").addEventListener("click", () => {
      item.innerHTML = `
        <div class="edit-form">
          <input class="edit-name" type="text" maxlength="500">
          <input class="edit-date" type="date" value="${entry.date || ""}">
          <input class="edit-initials" type="text" maxlength="5" placeholder="Initials" value="${entry.initials}">
          <button class="save-button">Save</button>
          <button class="cancel-button">Cancel</button>
        </div>
      `;
      const nameField = item.querySelector(".edit-name");
      nameField.value = entry.name;
      nameField.focus();
      nameField.select();

      const saveEdit = async () => {
        const name = nameField.value.trim();
        if (!name) return nameField.focus();
        const saveButton = item.querySelector(".save-button");
        saveButton.disabled = true;
        try {
          await saveEntry(entry, {
            name,
            date: item.querySelector(".edit-date").value,
            initials: cleanInitials(item.querySelector(".edit-initials").value),
          });
          renderEntries();
          setStatus();
        } catch (error) {
          setStatus(`Could not save entry: ${error.message}`, true);
          saveButton.disabled = false;
        }
      };
      item.querySelector(".save-button").addEventListener("click", saveEdit);
      item.querySelector(".cancel-button").addEventListener("click", renderEntries);
      item.querySelectorAll("input").forEach(field => field.addEventListener("keydown", event => {
        if (event.key === "Enter") saveEdit();
        if (event.key === "Escape") renderEntries();
      }));
    });
    entriesContainer.appendChild(item);
  });
}

async function loadEntries() {
  setStatus("Loading...");
  try {
    entries = await apiRequest("/api/entries");
    renderEntries();
    setStatus();
  } catch (error) {
    setStatus(`Could not load entries: ${error.message}`, true);
  }
}

async function loadVersion() {
  try {
    const result = await apiRequest("/api/version");
    appVersion.textContent = result.version || "unknown";
  } catch (_error) {
    appVersion.textContent = "unknown";
  }
}

async function addEntry() {
  const name = entryInput.value.trim();
  if (!name) return entryInput.focus();
  addButton.disabled = true;
  setStatus("Adding...");
  try {
    const entry = await apiRequest("/api/entries", {
      method: "POST",
      body: JSON.stringify({
        name,
        date: dateInput.value,
        initials: cleanInitials(initialsInput.value),
      }),
    });
    entries.push(entry);
    entryInput.value = "";
    dateInput.value = "";
    initialsInput.value = "";
    renderEntries();
    setStatus();
    entryInput.focus();
  } catch (error) {
    setStatus(`Could not add entry: ${error.message}`, true);
  } finally {
    addButton.disabled = false;
  }
}

initialsInput.addEventListener("input", () => {
  initialsInput.value = cleanInitials(initialsInput.value);
});
addButton.addEventListener("click", addEntry);
entryInput.addEventListener("keydown", event => {
  if (event.key === "Enter") addEntry();
});
loadEntries();
loadVersion();
