// History: past questions with search, filter chips and pins, plus the advisor meeting pack (PDF and email text).
import { escapeHtml, renderMarkdown } from "./citations.js";
import { loadPlans, planState } from "./plan-view.js";
import { api, authHeaders, errorFrom } from "./session.js";

const el = (id) => document.getElementById(id);
const FILTERS = [["all", "All"], ["pinned", "Pinned"], ["prerequisites", "Prerequisites"], ["plans", "Plans"], ["timetable", "Timetable"]];
const ui = { filter: "all", q: "", data: null, timer: null };
const BOOKMARK = '<svg width="20" height="20" viewBox="0 0 24 24" fill="#F59E0B" aria-label="Pinned" role="img"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>';
const CHAT = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#4F576B" stroke-width="2" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';

// "Today" / "Yesterday" / "Earlier this week" / "Earlier", by calendar day in the student's time zone.
export function dateGroup(iso, now = new Date()) {
  const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((day(now) - day(new Date(iso))) / 86400000);
  if (diff <= 0) return "TODAY";
  if (diff === 1) return "YESTERDAY";
  return diff < 7 ? "EARLIER THIS WEEK" : "EARLIER";
}

export function groupTurns(turns, now = new Date()) {
  const groups = [];
  const add = (label, turn) => {
    let g = groups.find((x) => x.label === label);
    if (!g) groups.push((g = { label, turns: [] }));
    g.turns.push(turn);
  };
  turns.filter((t) => t.pinned).forEach((t) => add("PINNED", t));
  turns.filter((t) => !t.pinned).forEach((t) => add(dateGroup(t.ts, now), t));
  return groups;
}

function rowsFor(turn) {
  return turn.sources.map((s, i) => {
    const tail = s.heading.split(" > ").pop();
    const m = tail.match(/^([A-Z]{3}\d{3}[HY]\d)\s+(.*)/);
    return { n: i + 1, code: m ? m[1] : "", title: m ? m[2] : tail, chunk_id: s.chunk_id, heading: s.heading, url: s.url };
  });
}

function entryHtml(turn) {
  const rows = rowsFor(turn);
  const numbers = new Map(rows.map((r) => [r.chunk_id, r.n]));
  const body = renderMarkdown(turn.answer, numbers);
  const preview = turn.answer.replace(/\[cal:[^\]]*\]/g, "").replace(/\*\*/g, "").replace(/\s+/g, " ").trim().slice(0, 140);
  return (
    `<details class="glass entry" data-id="${escapeHtml(turn.id)}"><summary>${turn.pinned ? BOOKMARK : CHAT}` +
    `<span class="meta-col"><strong>${escapeHtml(turn.question)}</strong><span class="muted">${escapeHtml(preview)}${turn.sources.length ? ` · ${turn.sources.length} source${turn.sources.length === 1 ? "" : "s"}` : ""}</span></span></summary>` +
    `<div class="entry-body">${body}<div class="actions"><button type="button" class="btn" data-pin="${escapeHtml(turn.id)}" aria-pressed="${turn.pinned}">${turn.pinned ? "Unpin" : "Pin this answer"}</button>` +
    `<button type="button" class="btn" data-ask="${escapeHtml(turn.question)}">Ask again</button>` +
    `<button type="button" class="btn danger" data-del="${escapeHtml(turn.id)}">Delete</button></div></div></details>`
  );
}

function drawList() {
  const { turns, total } = ui.data;
  const root = el("history-list");
  if (!turns.length) {
    root.innerHTML = `<div class="glass empty"><p>${total ? "Nothing matches that search or filter." : "Your questions will show up here once you've asked Pip something."}</p></div>`;
    return;
  }
  root.innerHTML = groupTurns(turns)
    .map((g) => `<h2 class="eyebrow group">${g.label}</h2>${g.turns.map(entryHtml).join("")}`)
    .join("");
}

async function fetchHistory() {
  const params = new URLSearchParams({ q: ui.q, filter: ui.filter });
  ui.data = await api("GET", `/api/history?${params}`);
  drawList();
  drawPack();
}

// ---- advisor pack ----
function drawPack() {
  const plans = planState.plans;
  const plan = plans.find((p) => p.id === el("pack-plan")?.value) || plans[0];
  const terms = plan ? plan.terms.filter((t) => t.courses.length).map((t) => t.term) : [];
  const pinned = ui.data.pinned;
  el("pack-options").innerHTML =
    (plans.length
      ? `<label class="check"><input type="checkbox" id="pack-use-plan" checked> <span>Plan</span></label>` +
        `<label class="sr" for="pack-plan">Plan to include</label><select id="pack-plan">${plans.map((p) => `<option value="${p.id}"${p.id === plan.id ? " selected" : ""}>${escapeHtml(p.name)}</option>`).join("")}</select>`
      : `<p class="muted">No plan yet. <a href="#/plan">Make one</a> to include it.</p>`) +
    `<label class="check"><input type="checkbox" id="pack-pinned"${pinned ? " checked" : ""}${pinned ? "" : " disabled"}> <span>${pinned} pinned answer${pinned === 1 ? "" : "s"}, with sources</span></label>` +
    `<label class="check"><input type="checkbox" id="pack-critic" checked${plans.length ? "" : " disabled"}> <span>Critic notes${plan ? ` (${plan.review.items.filter((i) => i.level !== "ok").length} open)` : ""}</span></label>` +
    `<label class="check"><input type="checkbox" id="pack-tt"${terms.length ? "" : " disabled"}> <span>Timetable (sample data)</span></label>` +
    (terms.length ? `<label class="sr" for="pack-term">Timetable term</label><select id="pack-term">${terms.map((t) => `<option>${escapeHtml(t)}</option>`).join("")}</select>` : "");
}

function packBody() {
  const usePlan = el("pack-use-plan")?.checked;
  return {
    plan_id: usePlan ? el("pack-plan").value : null,
    include_pinned: el("pack-pinned").checked,
    include_critic: Boolean(usePlan && el("pack-critic").checked),
    timetable_term: usePlan && el("pack-tt").checked ? el("pack-term").value : null,
  };
}

function say(text, bad = false) {
  const s = el("pack-status");
  s.textContent = text;
  s.className = bad ? "form-error" : "form-ok";
}

async function downloadPdf() {
  say("Building your PDF…");
  try {
    const response = await fetch("/api/pack/pdf", { method: "POST", headers: authHeaders(), body: JSON.stringify(packBody()) });
    if (!response.ok) throw await errorFrom(response);
    const url = URL.createObjectURL(await response.blob());
    const link = Object.assign(document.createElement("a"), { href: url, download: "waypoint-advisor-pack.pdf" });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    say("Downloaded waypoint-advisor-pack.pdf.");
  } catch (err) {
    say(err.message, true);
  }
}

async function copyEmail() {
  try {
    const { text } = await api("POST", "/api/pack/email", packBody());
    el("pack-email").value = text;
    el("pack-email").hidden = false;
    try {
      await navigator.clipboard.writeText(text);
      say("Copied. Paste it into an email.");
    } catch {
      el("pack-email").select();
      say("Select the text below and copy it.");
    }
  } catch (err) {
    say(err.message, true);
  }
}

export async function showHistory() {
  try {
    await loadPlans(true);
    await fetchHistory();
  } catch (err) {
    el("history-list").innerHTML = `<p class="form-error">${escapeHtml(err.message)}</p>`;
  }
}

export function wireHistory() {
  el("history-search").addEventListener("input", (e) => {
    clearTimeout(ui.timer);
    ui.timer = setTimeout(() => {
      ui.q = e.target.value;
      fetchHistory();
    }, 200);
  });
  el("history-filters").innerHTML = FILTERS.map(([id, label]) => `<button type="button" class="chipbtn" data-filter="${id}" aria-pressed="${id === "all"}">${label}</button>`).join("");
  el("history-filters").addEventListener("click", (e) => {
    const b = e.target.closest("[data-filter]");
    if (!b) return;
    ui.filter = b.dataset.filter;
    el("history-filters").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    fetchHistory();
  });
  el("history-list").addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.pin) {
      await api("POST", `/api/history/${t.dataset.pin}/pin`, { pinned: t.getAttribute("aria-pressed") !== "true" });
      fetchHistory();
    } else if (t.dataset.del) {
      await api("DELETE", `/api/history/${t.dataset.del}`);
      fetchHistory();
    } else if (t.dataset.ask) {
      window.dispatchEvent(new CustomEvent("waypoint:ask", { detail: t.dataset.ask }));
    } else if (t.classList.contains("cite")) {
      const turn = ui.data.turns.find((x) => x.id === t.closest(".entry").dataset.id);
      const sources = rowsFor(turn);
      window.dispatchEvent(new CustomEvent("waypoint:open-source", { detail: { sources, n: Number(t.dataset.source) } }));
    }
  });
  el("pack-options").addEventListener("change", (e) => {
    if (e.target.id === "pack-plan") drawPack();
  });
  el("pack-pdf").addEventListener("click", downloadPdf);
  el("pack-copy").addEventListener("click", copyEmail);
}
