"use strict";
const $ = (id) => document.getElementById(id);
let me,
  state,
  selected = null,
  stream,
  dragging = null,
  deferredState = null,
  busy = 0,
  activeTab = "queue";
let sampledAt = performance.now();
let queueSignature = "",
  librarySignature = "",
  logSignature = "";
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const duration = (n) =>
  Number.isFinite(Number(n)) && n !== null
    ? `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, "0")}`
    : "—";
function image(url, cls = "cover") {
  try {
    const parsed = new URL(url, location.origin);
    if (url && (parsed.protocol === "https:" || (parsed.origin === location.origin && parsed.pathname.startsWith("/api/playlists/"))))
      return `<img class="${cls}" src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`;
  } catch {}
  return `<div class="${cls} placeholder">♫</div>`;
}
function toast(message, success = false) {
  const node = document.createElement("div");
  node.className = "toast" + (success ? " success" : "");
  node.textContent = message;
  $("toasts").append(node);
  setTimeout(() => node.remove(), 6500);
}
async function request(path, data) {
  const response = await fetch(
    path,
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": data instanceof Blob ? data.type || "application/octet-stream" : "application/json",
            "X-CSRF-Token": me?.csrf || "",
          },
          body: data instanceof Blob ? data : JSON.stringify(data),
        },
  );
  const result = await response.json();
  if (!response.ok) {
    if ([401, 403].includes(response.status)) signedOut(result.error);
    throw new Error(result.error || "Request failed.");
  }
  return result;
}
async function action(data) {
  busy++;
  document.body.classList.add("working");
  try {
    const result = await request("/api/action", data);
    accept(result);
    return result;
  } catch (error) {
    toast(error.message);
    try {
      accept(await request("/api/state"));
    } catch {}
    throw error;
  } finally {
    if (--busy === 0) document.body.classList.remove("working");
  }
}
function signedOut(error = "") {
  stream?.close();
  $("app").hidden = true;
  $("login").hidden = false;
  $("login-error").textContent = error;
}
function tab(name) {
  activeTab = name;
  for (const item of ["queue", "playlists", "log"]) {
    $(`${item}-page`).hidden = item !== name;
    $(`tab-${item}`).setAttribute("aria-selected", item === name);
  }
  localStorage.setItem("kirbo-tab", name);
  if (state) render();
}
document
  .querySelectorAll("[data-tab]")
  .forEach((button) => (button.onclick = () => tab(button.dataset.tab)));
document.querySelector(".brand").onclick = (event) => {
  event.preventDefault();
  tab("queue");
};
function accept(next) {
  const previous = deferredState || state;
  if (previous && next.instance === previous.instance && next.revision < previous.revision) return;
  if (dragging) {
    deferredState = next;
    return;
  }
  state = next;
  sampledAt = performance.now();
  render();
}
const pauseIcon = '<svg class="pause-glyph" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14M16 5v14"/></svg>';
const playIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 11 7-11 7z"/></svg>';
function playingIcon() {
  if (state.loading) return '<span aria-label="Preparing audio">◌</span>';
  if (state.paused) return `<span class="paused-indicator" aria-label="Paused">${pauseIcon}</span>`;
  return '<span class="sound-bars" aria-label="Playing"><i></i><i></i><i></i><i></i></span>';
}
// Keep existing image and row elements attached when shared state changes.
function syncHTML(parent, html) {
  if (parent.innerHTML === html) return;
  const template = document.createElement("template");
  template.innerHTML = html;
  function patchChildren(target, source) {
    const key = (node) => node.nodeType === 1 && (node.dataset.id || node.dataset.playlist);
    const keyed = new Map([...target.childNodes].filter(key).map(node => [key(node), node]));
    const children = [...source.childNodes];
    children.forEach((next, i) => {
      let old = key(next) ? keyed.get(key(next)) : target.childNodes[i];
      if (!key(next) && key(old || {})) old = null;
      if (!old) {
        target.insertBefore(next, target.childNodes[i] || null);
        return;
      }
      if (old !== target.childNodes[i]) target.insertBefore(old, target.childNodes[i] || null);
      if (old.nodeType !== next.nodeType || old.nodeName !== next.nodeName) {
        old.replaceWith(next);
      } else if (old.nodeType === Node.TEXT_NODE) {
        if (old.textContent !== next.textContent) old.textContent = next.textContent;
      } else if (old.nodeType === Node.ELEMENT_NODE) {
        for (const attr of [...old.attributes]) if (!next.hasAttribute(attr.name)) old.removeAttribute(attr.name);
        for (const attr of [...next.attributes]) if (old.getAttribute(attr.name) !== attr.value) old.setAttribute(attr.name, attr.value);
        patchChildren(old, next);
      }
    });
    while (target.childNodes.length > children.length) target.lastChild.remove();
  }
  patchChildren(parent, template.content);
}
function rowHTML(row, index, playlist = false, current = false) {
  return `<div class="track-row${current ? " current" : ""}" data-id="${esc(row.id)}" ${current ? "" : 'tabindex="0"'}>
  <span class="track-number" ${current ? "" : 'title="Drag to reorder; Alt+Arrow keys also move a song"'}>${current ? playingIcon() : index + 1}</span>
  <div class="track-info">${image(row.thumbnail)}<div class="track-text"><div class="track-title" title="${esc(row.title)}">${esc(row.title)}</div><div class="track-artist">${current ? (state.loading ? "Downloading audio…" : state.paused ? "Paused" : "Playing now") : esc(row.artist || "YouTube · shared with the server")}</div></div></div>
  ${playlist ? '<button class="row-add" data-row-action="add">＋ Queue</button>' : `<span class="track-by">${esc(row.added_by || "Kirbo")}</span>`}
  <span class="duration">${duration(row.duration)}</span>${current ? "<span></span>" : '<button class="row-delete" data-row-action="remove" aria-label="Remove song">×</button>'}</div>`;
}
function render() {
  $("queue-count").textContent = state.queue.length;
  $("total-duration").textContent =
    `${state.queue.length} ${state.queue.length === 1 ? "song" : "songs"} · ${duration(state.queue.reduce((s, r) => s + (Number(r.duration) || 0), 0))}`;
  const qsig = JSON.stringify(state.queue);
  if (activeTab === "queue" && qsig !== queueSignature) {
    renderRows("queue-list", state.queue, false);
    queueSignature = qsig;
  }
  syncHTML($("current-row"), state.current
    ? rowHTML(state.current, 0, false, true)
    : "");
  $("now-playing").hidden = !state.current;
  $("queue-empty").hidden = Boolean(state.queue.length || state.current);
  const current = state.current;
  syncHTML($("player-track"), current
    ? `${image(current.thumbnail)}<div><b>${esc(current.title)}</b><small>${state.loading ? "Preparing audio…" : state.paused ? "Paused" : esc(current.artist || "Playing in Discord")}</small></div>`
    : '<div class="cover placeholder">♫</div><div><b>Ready when you are</b><small>Join a voice channel and add a song</small></div>');
  syncHTML($("pause-button"), state.paused || !current ? playIcon : pauseIcon);
  $("pause-button").setAttribute("aria-label", state.paused ? "Resume playback" : "Pause playback");
  $("pause-button").dataset.control = state.paused ? "resume" : "pause";
  $("pause-button").disabled = !current || state.loading;
  $("skip-button").disabled = !current;
  updateProgress();
  $("voice-status").textContent = state.channel
    ? `# ${state.channel}`
    : "Not connected";
  const psig = JSON.stringify([selected, state.playlists]);
  if (activeTab === "playlists" && psig !== librarySignature) {
    renderLibrary();
    librarySignature = psig;
  }
  const lsig = JSON.stringify(state.logs);
  if (activeTab === "log" && lsig !== logSignature) {
    logSignature = lsig;
    $("activity-list").innerHTML = state.logs.length
      ? state.logs
          .map(
            (e) =>
              `<div class="activity-row"><span class="activity-icon">♫</span><div class="activity-copy"><b>${esc(e.actor)}</b> ${esc(e.message)}</div><time datetime="${new Date(e.at * 1000).toISOString()}" title="${esc(new Date(e.at * 1000).toLocaleString())}">${esc(new Date(e.at * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }))}</time></div>`,
          )
          .join("")
      : '<div class="empty"><h3>Quiet in here. For now.</h3><p>The server’s music activity will appear here.</p></div>';
  }
}
function updateProgress() {
  if (!state) return;
  const total = Math.max(0, Number(state.current?.duration) || 0);
  const advancing = state.current && !state.loading && !state.paused;
  let elapsed = Math.max(0, Number(state.position) || 0) + (advancing ? (performance.now() - sampledAt) / 1000 : 0);
  elapsed = total ? Math.min(total, elapsed) : 0;
  $("elapsed").textContent = duration(Math.floor(elapsed));
  $("remaining").textContent = total ? `−${duration(Math.ceil(total - elapsed))}` : "0:00";
  $("progress-fill").style.width = `${total ? elapsed / total * 100 : 0}%`;
  $("song-progress").setAttribute("aria-valuemax", total || 1);
  $("song-progress").setAttribute("aria-valuenow", elapsed);
  $("song-progress").setAttribute("aria-valuetext", `${duration(Math.floor(elapsed))} of ${duration(total)}`);
}
setInterval(updateProgress, 250);
function animateLayout(container, mutate) {
  const old = new Map(
    [...container.children].map((n) => [
      n.dataset.id,
      n.getBoundingClientRect(),
    ]),
  );
  mutate();
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  for (const node of container.children) {
    const prev = old.get(node.dataset.id);
    if (prev) {
      const now = node.getBoundingClientRect(),
        y = prev.top - now.top;
      if (y)
        node.animate(
          [{ transform: `translateY(${y}px)` }, { transform: "translateY(0)" }],
          { duration: 180, easing: "ease-out" },
        );
    }
  }
}
function renderRows(id, rows, playlist) {
  const container = $(id),
    active = document.activeElement?.closest(".track-row")?.dataset.id;
  animateLayout(container, () => {
    syncHTML(container, rows.map((r, i) => rowHTML(r, i, playlist)).join(""));
  });
  if (active)
    [...container.children]
      .find((n) => n.dataset.id === active)
      ?.focus({ preventScroll: true });
  container.onmousedown = (event) => beginDrag(event, container, playlist);
  container.onpointerdown = (event) => {
    if (event.pointerType !== "mouse") beginDrag(event, container, playlist);
  };
  container.ondragstart = (event) => event.preventDefault();
  container.onclick = (event) => {
    const button = event.target.closest("[data-row-action]"),
      node = event.target.closest(".track-row");
    if (!button || !node) return;
    const row = rows.find((r) => r.id === node.dataset.id);
    if (button.dataset.rowAction === "add")
      action({ action: "add", query: row.query })
        .then(() => toast("Song added to the queue.", true))
        .catch(() => {});
    else if (playlist) savePlaylist(rows.filter((r) => r.id !== row.id));
    else {
      state.queue = state.queue.filter((r) => r.id !== row.id);
      render();
      action({ action: "remove", id: row.id }).catch(() => {});
    }
  };
  container.oncontextmenu = (event) => {
    const node = event.target.closest(".track-row");
    if (!node) return;
    event.preventDefault();
    const row = rows.find((r) => r.id === node.dataset.id);
    const items = [
      [
        "Add song to queue",
        () => action({ action: "add", query: row.query }).catch(() => {}),
      ],
      [
        "Play next",
        () =>
          action({ action: "add", query: row.query, next: true }).catch(
            () => {},
          ),
      ],
    ];
    if (playlist)
      items.push(
        ["Edit song", () => editSong(row)],
        [
          "Remove from playlist",
          () => savePlaylist(rows.filter((r) => r.id !== row.id)),
        ],
      );
    context(event, items);
  };
  container.onkeydown = (event) => {
    if (!event.altKey || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
    const node = event.target.closest(".track-row");
    if (!node) return;
    event.preventDefault();
    const ordered = [...rows],
      i = ordered.findIndex((r) => r.id === node.dataset.id),
      j = i + (event.key === "ArrowUp" ? -1 : 1);
    if (j < 0 || j >= ordered.length) return;
    [ordered[i], ordered[j]] = [ordered[j], ordered[i]];
    if (playlist) savePlaylist(ordered);
    else
      action({
        action: "reorder",
        ids: ordered.map((r) => r.id),
        revision: state.queue_revision,
      }).catch(() => {});
  };
}
function beginDrag(event, container, playlist) {
  if (dragging || event.button !== 0 || event.target.closest("button,input")) return;
  const node = event.target.closest(".track-row");
  if (!node) return;
  event.preventDefault();
  node.focus({ preventScroll: true });
  const moveEvent = event.type === "mousedown" ? "mousemove" : "pointermove",
    endEvent = event.type === "mousedown" ? "mouseup" : "pointerup";
  let startX = event.clientX,
    startY = event.clientY,
    original = [...container.children].map((n) => n.dataset.id),
    ghost,
    offset;
  const revision = playlist
    ? state.playlists.find((p) => p.id === selected)?.revision
    : state.queue_revision;
  function move(e) {
    if (!ghost && Math.hypot(e.clientX - startX, e.clientY - startY) < 7)
      return;
    e.preventDefault();
    if (!ghost) {
      const rect = node.getBoundingClientRect();
      offset = startY - rect.top;
      ghost = node.cloneNode(true);
      ghost.classList.add("drag-ghost");
      ghost.style.width = rect.width + "px";
      ghost.style.left = rect.left + "px";
      document.body.append(ghost);
      node.classList.add("dragging");
      dragging = { id: node.dataset.id };
    }
    ghost.style.top = e.clientY - offset + "px";
    const target = [...container.children].find((n) => {
      const rect = n.getBoundingClientRect();
      return n !== node && e.clientY >= rect.top && e.clientY < rect.bottom;
    });
    if (target)
      animateLayout(container, () => {
        const rect = target.getBoundingClientRect();
        container.insertBefore(
          node,
          e.clientY < rect.top + rect.height / 2 ? target : target.nextSibling,
        );
      });
    [...container.children].forEach((row, i) => { row.querySelector(".track-number").textContent = i + 1; });
    if (e.clientY > innerHeight - 110) window.scrollBy(0, 13);
    else if (e.clientY < 100) window.scrollBy(0, -13);
  }
  async function end() {
    document.removeEventListener(moveEvent, move);
    document.removeEventListener(endEvent, end);
    document.removeEventListener("pointercancel", cancel);
    if (!ghost) return;
    ghost.remove();
    node.classList.remove("dragging");
    const ids = [...container.children].map((n) => n.dataset.id);
    try {
      if (ids.join() !== original.join()) {
        if (playlist) {
          const p = state.playlists.find((p) => p.id === selected);
          if (p) await savePlaylist(ids.map((id) => p.tracks.find((r) => r.id === id)), revision);
        } else {
          await action({ action: "reorder", ids, revision }).catch(() => {});
        }
      }
    } finally {
      dragging = null;
      queueSignature = librarySignature = "";
      if (deferredState) {
        const next = deferredState;
        deferredState = null;
        accept(next);
      } else render();
    }
  }
  function cancel() {
    ghost?.remove();
    node.classList.remove("dragging");
    dragging = null;
    document.removeEventListener(moveEvent, move);
    document.removeEventListener(endEvent, end);
    document.removeEventListener("pointercancel", cancel);
    queueSignature = librarySignature = "";
    if (deferredState) {
      const next = deferredState;
      deferredState = null;
      accept(next);
    } else render();
  }
  document.addEventListener(moveEvent, move, { passive: false });
  document.addEventListener(endEvent, end, { once: true });
  document.addEventListener("pointercancel", cancel, { once: true });
}
function context(event, items) {
  const menu = $("context-menu");
  menu.replaceChildren();
  for (const [label, fn] of items) {
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = () => {
      menu.hidden = true;
      fn();
    };
    menu.append(b);
  }
  menu.hidden = false;
  menu.style.left = Math.min(event.clientX, innerWidth - 210) + "px";
  menu.style.top =
    Math.min(event.clientY, innerHeight - menu.offsetHeight - 15) + "px";
  menu.querySelector("button")?.focus();
}
document.addEventListener("click", (event) => {
  if (!event.target.closest("#context-menu")) $("context-menu").hidden = true;
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") $("context-menu").hidden = true;
});
function playlistArt(p) {
  return image(p.cover ? `/api/playlists/${encodeURIComponent(p.id)}/cover?v=${encodeURIComponent(p.cover)}` : p.tracks[0]?.thumbnail, "playlist-cover");
}
function renderLibrary() {
  $("library-empty").hidden = Boolean(state.playlists.length);
  syncHTML($("playlist-grid"), state.playlists
    .map((p) => {
      return `<button class="playlist-card" data-playlist="${esc(p.id)}"><div class="art">${playlistArt(p)}</div><h3>${esc(p.name)}</h3><p>${p.tracks.length} songs · Shared playlist</p></button>`;
    })
    .join(""));
  $("playlist-grid")
    .querySelectorAll("[data-playlist]")
    .forEach(
      (n) =>
        (n.onclick = () => {
          selected = n.dataset.playlist;
          renderLibrary();
        }),
    );
  const p = state.playlists.find((p) => p.id === selected);
  if (!p) selected = null;
  $("library-view").hidden = Boolean(p);
  $("playlist-detail").hidden = !p;
  if (p) {
    $("playlist-title").textContent = p.name;
    $("playlist-meta").textContent =
      `${p.tracks.length} songs · Everyone can contribute`;
    syncHTML($("playlist-art"), `${playlistArt(p)}<span class="choose-photo"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m16 3 5 5-12 12-6 1 1-6zM14 5l5 5"/></svg><span>Choose photo</span></span>`);
    renderRows("playlist-tracks", p.tracks, true);
  }
}
$("playlist-art").onclick = () => $("playlist-photo").click();
$("playlist-photo").onchange = async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  const p = state.playlists.find((p) => p.id === selected);
  if (!file || !p) return;
  if (file.size > 5 * 1024 * 1024) {
    toast("Choose a photo smaller than 5 MB.");
    return;
  }
  $("playlist-art").disabled = true;
  $("playlist-art").setAttribute("aria-busy", "true");
  try {
    accept(await request(`/api/playlists/${encodeURIComponent(p.id)}/cover?revision=${p.revision}`, file));
    toast("Playlist photo updated for everyone.", true);
  } catch (error) {
    toast(error.message);
    try { accept(await request("/api/state")); } catch {}
  } finally {
    $("playlist-art").disabled = false;
    $("playlist-art").removeAttribute("aria-busy");
  }
};
async function savePlaylist(rows, revision) {
  const p = state.playlists.find((p) => p.id === selected);
  if (!p) return;
  try {
    await action({
      action: "save_playlist",
      id: p.id,
      name: p.name,
      tracks: rows,
      revision: revision ?? p.revision,
    });
  } catch {}
}
function modal(title, fields, submit, label = "Save") {
  $("editor-title").textContent = title;
  $("editor-submit").textContent = label;
  $("editor-fields").replaceChildren();
  for (const f of fields) {
    const label = document.createElement("label");
    label.className = "field";
    label.append(document.createTextNode(f.label));
    const input = document.createElement("input");
    input.name = f.name;
    input.value = f.value || "";
    input.required = f.required !== false;
    input.maxLength = f.maxLength || 500;
    if (f.type) input.type = f.type;
    label.append(input);
    if (f.hint) {
      const small = document.createElement("small");
      small.textContent = f.hint;
      label.append(small);
    }
    $("editor-fields").append(label);
  }
  $("editor-form").onsubmit = async (event) => {
    event.preventDefault();
    $("editor-submit").disabled = true;
    try {
      await submit(Object.fromEntries(new FormData(event.target)));
      $("editor").close();
    } catch (error) {
      if (!error.message) toast("Could not save.");
    } finally {
      $("editor-submit").disabled = false;
    }
  };
  $("editor").showModal();
  $("editor-fields").querySelector("input")?.focus();
}
function editSong(row) {
  modal(
    "Edit song",
    [
      { name: "title", label: "Title", value: row.title },
      { name: "query", label: "YouTube link or search", value: row.query },
    ],
    async (values) => {
      const p = state.playlists.find((p) => p.id === selected);
      await action({
        action: "playlist_song",
        id: p.id,
        revision: p.revision,
        song_id: row.id,
        ...values,
      });
    },
  );
}
$("close-editor").onclick = $("cancel-editor").onclick = () =>
  $("editor").close();
$("new-playlist").onclick = () =>
  modal(
    "New shared playlist",
    [{ name: "name", label: "Playlist name", maxLength: 100 }],
    async ({ name }) => {
      await action({ action: "save_playlist", name, tracks: [] });
      toast("Playlist created.", true);
    },
  );
$("import-playlist").onclick = () =>
  modal(
    "Import from Spotify",
    [
      { name: "name", label: "Save as", maxLength: 100 },
      {
        name: "url",
        label: "Spotify playlist link",
        type: "url",
        hint: "Spotify playlists must be available to the configured Spotify application.",
      },
    ],
    async ({ name, url }) => {
      await action({ action: "import_spotify", name, url });
      toast("Playlist imported.", true);
    },
    "Import playlist",
  );
$("back-library").onclick = () => {
  selected = null;
  renderLibrary();
};
$("rename-playlist").onclick = () => {
  const p = state.playlists.find((p) => p.id === selected);
  modal(
    "Rename playlist",
    [{ name: "name", label: "Name", value: p.name, maxLength: 100 }],
    async ({ name }) =>
      action({
        action: "save_playlist",
        id: p.id,
        name,
        tracks: p.tracks,
        revision: p.revision,
      }),
  );
};
$("delete-playlist").onclick = () => {
  const p = state.playlists.find((p) => p.id === selected);
  modal(
    "Delete playlist",
    [
      {
        name: "confirm",
        label: `Type ${p.name} to delete this shared playlist`,
        maxLength: 100,
      },
    ],
    async ({ confirm }) => {
      if (confirm !== p.name) {
        toast("Playlist name does not match.");
        throw Error("mismatch");
      }
      await action({
        action: "delete_playlist",
        id: p.id,
        revision: p.revision,
      });
    },
  );
};
$("play-playlist").onclick = () =>
  action({ action: "queue_playlist", id: selected })
    .then(() => toast("Playlist added to the queue.", true))
    .catch(() => {});
$("shuffle-playlist").onclick = () =>
  action({ action: "queue_playlist", id: selected, shuffle: true })
    .then(() => toast("Playlist shuffled and added to the queue.", true))
    .catch(() => {});
$("playlist-add").onsubmit = (event) => {
  event.preventDefault();
  const input = $("playlist-query"),
    p = state.playlists.find((p) => p.id === selected),
    query = input.value.trim();
  if (!query) return;
  action({ action: "playlist_song", id: p.id, revision: p.revision, query })
    .then(() => {
      input.value = "";
    })
    .catch(() => {});
};
$("direct-playlist").onsubmit = (event) => {
  event.preventDefault();
  action({ action: "queue_playlist", url: $("spotify-direct").value })
    .then(() => {
      toast("Adding Spotify playlist songs.", true);
      $("spotify-direct").value = "";
    })
    .catch(() => {});
};
$("search-form").onsubmit = async (event) => {
  event.preventDefault();
  const input = $("song-query"),
    button = event.target.querySelector("button"),
    query = input.value.trim();
  if (!query) return;
  button.disabled = true;
  button.textContent = "Searching…";
  try {
    await action({ action: "add", query, next: $("next-up").checked });
    input.value = "";
  } catch {
  } finally {
    button.disabled = false;
    button.textContent = "＋ Add song";
  }
};
document
  .querySelectorAll("[data-control]")
  .forEach(
    (b) =>
      (b.onclick = () => action({ action: b.dataset.control }).catch(() => {})),
  );
$("logout").onclick = async () => {
  try {
    await request("/api/logout", {});
  } finally {
    signedOut();
  }
};
async function boot() {
  try {
    me = await request("/api/me");
    $("username").textContent = me.username;
    $("avatar").src = me.avatar;
    $("login").hidden = true;
    $("app").hidden = false;
    tab(
      ["queue", "playlists", "log"].includes(localStorage.getItem("kirbo-tab"))
        ? localStorage.getItem("kirbo-tab")
        : "queue",
    );
    accept(await request("/api/state"));
    stream = new EventSource("/api/events");
    stream.onopen = () => {
      $("connection").textContent = "Connected to Kirbo";
    };
    stream.onmessage = (e) => {
      $("connection").textContent = "Connected to Kirbo";
      accept(JSON.parse(e.data));
    };
    stream.addEventListener("signed-out", () => signedOut("Your Discord session or server membership needs to be verified. Sign in again."));
    stream.addEventListener("verification", () => { $("connection").textContent = "Waiting for Discord verification…"; });
    stream.onerror = () => {
      $("connection").textContent = "Reconnecting…";
      request("/api/me").catch(() => {});
    };
  } catch (error) {
    signedOut(
      error.message === "Sign in with Discord to continue."
        ? ""
        : error.message,
    );
  }
}
boot();
