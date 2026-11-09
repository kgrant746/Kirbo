"use strict";
// Loaded only by preview_frontend.py, before the production script.
(() => {
  const song = (id, title, video, length) => ({id, title, query: `https://youtu.be/${video}`, duration: length,
    thumbnail: `https://i.ytimg.com/vi/${video}/hqdefault.jpg`, artist: "Team Rocket favorites", added_by: "Preview friend"});
  const songs = [song("a", "Ain’t It Fun", "EFEmTsfFL5A", 297), song("b", "R U Mine?", "VQH8ZTgna3Q", 202), song("c", "Disturbia", "E1mU6h4Xdxc", 239)];
  const initial = {instance: "preview", revision: 1, queue_revision: 1, current: song("current", "Cool For The Summer", "il9nqWw9W3Y", 233),
    queue: structuredClone(songs), playlists: [{id:"mix", name:"Team Rocket favorites", revision:1, tracks:structuredClone(songs), cover:null}],
    logs: [], loading:false, paused:false, channel:"Jubilife City", position:37};
  let fixture = structuredClone(initial), events, rejectReorder = false;
  const response = (data, status = 200) => new Response(JSON.stringify(data), {status, headers:{"Content-Type":"application/json"}});
  window.fetch = async (path, options = {}) => {
    if (path === "/api/me") return response({id:"1", username:"Preview friend", avatar:"/static/icon.svg", creator_avatar:"/static/icon.svg", csrf:"test"});
    if (path === "/api/state") return response(fixture);
    if (path === "/api/action") {
      const data = JSON.parse(options.body);
      if (data.action === "reorder") {
        if (rejectReorder) return response({error:"Simulated stale reorder"}, 409);
        fixture.queue = data.ids.map(id => fixture.queue.find(row => row.id === id));
        fixture.queue_revision++;
      } else if (data.action === "queue_playlist") {
        fixture.queue.push(...songs.map((row, i) => ({...row, id:`added-${i}`})));
        fixture.queue_revision++;
      } else if (["pause", "resume"].includes(data.action)) fixture.paused = data.action === "pause";
      fixture.revision++;
      return response(fixture);
    }
    throw Error(`Unexpected preview request: ${path}`);
  };
  window.EventSource = class {
    constructor() { events = this; this.listeners = {}; setTimeout(() => this.onopen?.(), 0); }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    close() {}
  };
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  const check = (condition, message) => { if (!condition) throw Error(message); };
  const publish = () => events.onmessage({data:JSON.stringify(fixture)});
  const numbers = () => [...document.querySelectorAll("#queue-list .track-number")].map(node => node.textContent).join();
  const ids = () => [...document.querySelectorAll("#queue-list .track-row")].map(node => node.dataset.id).join();
  async function dragFirstAfterSecond() {
    const rows = document.querySelectorAll("#queue-list .track-row");
    rows[0].scrollIntoView({block:"center"});
    const from = rows[0].getBoundingClientRect(), to = rows[1].getBoundingClientRect();
    const x = from.left + 10, y = from.top + from.height / 2;
    rows[0].dispatchEvent(new MouseEvent("mousedown", {bubbles:true, clientX:x, clientY:y, button:0}));
    document.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:x, clientY:to.bottom-8, button:0}));
    document.dispatchEvent(new MouseEvent("mouseup", {bubbles:true, clientX:x, clientY:to.bottom-8, button:0}));
    for (let i = 0; i < 30 && dragging; i++) await tick();
    check(!dragging, "Drag operation did not finish");
  }
  document.addEventListener("DOMContentLoaded", async () => {
    const results = document.createElement("output");
    results.id = "frontend-check-results";
    results.hidden = true;
    document.body.append(results);
    try {
      for (let i = 0; i < 30 && !events; i++) await tick();
      check(events && state, "Website failed to start");
      tab("queue");
      events.listeners.verification();
      publish();
      check(document.getElementById("connection").textContent === "Synced with Discord", "Connection label did not recover with the stream");
      check(document.querySelector(".creator-credit").textContent.includes("CosmicCats"), "Creator credit is missing");
      check(document.querySelector(".brand span").textContent === "POYO POYO", "Brand tagline is incorrect");
      check(!document.querySelector(".live-badge") && !document.querySelector(".page-heading p") && !document.querySelector(".sidebar-note p"), "Removed flavor text is still present");
      check(!document.querySelector(".page-heading .eyebrow, .nav-dot, #library-view .section-heading p, #log-page .section-heading p"), "Removed labels or navigation dot are still present");
      check(document.getElementById("tab-log").textContent.trim() === "Logs", "Logs tab label is incorrect");
      const centerX = node => { const rect = node.getBoundingClientRect(); return rect.left + rect.width / 2; };
      const checkPlayerCenter = () => check(Math.abs(centerX(document.getElementById("pause-button")) - centerX(document.getElementById("song-progress"))) < 1, "Play/Pause is not centered on the progress line");
      checkPlayerCenter();
      const totalTime = document.getElementById("track-duration").textContent;
      fixture.channel = null; fixture.revision++; publish();
      check(document.getElementById("voice-status").textContent === "Not in a voice channel", "Disconnected voice label is incorrect");
      fixture.channel = initial.channel; fixture.revision++; publish();
      const playerImage = document.querySelector("#player-track img");
      const currentImage = document.querySelector("#current-row img");
      const queueImages = [...document.querySelectorAll("#queue-list img")];
      for (let i = 0; i < 5; i++) {
        fixture.position++;
        publish();
        tab("playlists"); tab("log"); tab("queue");
      }
      check(totalTime === "3:53" && document.getElementById("track-duration").textContent === totalTime, "Total-duration label changed as playback advanced");
      fixture.current.duration = 4000; fixture.position = 9; fixture.revision++; publish();
      checkPlayerCenter();
      check(document.getElementById("track-duration").textContent === "66:40", "Long total-duration label is incorrect");
      fixture.current.duration = initial.current.duration; fixture.revision++; publish();
      check(document.querySelector("#player-track img") === playerImage, "Footer thumbnail was replaced on heartbeat/tab switch");
      check(document.querySelector("#current-row img") === currentImage, "Current thumbnail was replaced on heartbeat");
      check([...document.querySelectorAll("#queue-list img")].every((image, i) => image === queueImages[i]), "Queue thumbnails were replaced");
      tab("playlists"); selected = "mix"; renderLibrary();
      const playlistImages = [...document.querySelectorAll("#playlist-tracks img")];
      fixture.playlists[0].tracks[1].duration++;
      fixture.revision++;
      publish();
      check([...document.querySelectorAll("#playlist-tracks img")].every((image, i) => image === playlistImages[i]), "Playlist metadata refresh replaced thumbnails");
      await action({action:"queue_playlist", id:"mix"});
      tab("queue");
      check(document.querySelectorAll("#queue-list .track-row").length === 6, "Queued playlist required a refresh");
      check(document.querySelector("#player-track img") === playerImage, "Queue changes replaced the footer image");
      const original = ids();
      rejectReorder = true;
      await dragFirstAfterSecond();
      check(ids() === original, "Rejected drag failed to restore server order");
      check(numbers() === "1,2,3,4,5,6", "Rejected drag left incorrect row numbers");
      rejectReorder = false;
      await dragFirstAfterSecond();
      check(ids() !== original, "Successful drag did not reorder songs");
      check(numbers() === "1,2,3,4,5,6", "Successful drag left incorrect row numbers");
      fixture.paused = true; fixture.revision++; publish();
      check(document.querySelector(".paused-indicator") && !document.querySelector(".sound-bars"), "Paused indicator did not replace sound bars");
      const elapsed = document.getElementById("elapsed").textContent;
      await new Promise(resolve => setTimeout(resolve, 300));
      check(document.getElementById("elapsed").textContent === elapsed, "Progress advanced while paused");
      fixture.current = null; fixture.queue = []; fixture.position = 0; fixture.revision++; publish();
      check(document.getElementById("now-playing").hidden, "Last track remained in Now playing");
      check(!document.getElementById("queue-empty").hidden && document.getElementById("player-track").textContent.includes("Nothing playing"), "Idle state did not reset");
      fixture = structuredClone(initial); fixture.instance = "restarted"; fixture.revision = 0; publish();
      check(!document.getElementById("now-playing").hidden, "Restart with a lower revision was ignored");
      check(document.querySelectorAll(".sound-bars i").length === 4, "Playing indicator should contain four bars");
      document.getElementById("toasts").replaceChildren();
      results.textContent = "PASS: labels, centered controls, static total timer, stable thumbnails, live updates, drag rollback, pause/progress, idle cleanup, bot restart";
      console.log(results.textContent);
      window.scrollTo(0, 0);
    } catch (error) {
      results.hidden = false;
      results.textContent = `FAIL: ${error.message}`;
      console.error(results.textContent);
    }
  });
})();
