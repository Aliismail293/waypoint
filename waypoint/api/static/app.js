// Waypoint chat UI. Sign-in uses Google Identity Services: the button returns an ID token (JWT) to this page,
// which sends it to the API as a Bearer token. No redirect flow; the token lives only in memory.
"use strict";

const state = { idToken: null, sessionId: null, advisorUrl: "#" };
const $ = (id) => document.getElementById(id);

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Minimal, safe markdown: the text is escaped first, then a few patterns are turned into tags.
function renderMarkdown(text, citationNumbers) {
  const lines = escapeHtml(text).split("\n");
  const html = [];
  let inList = false;
  for (const raw of lines) {
    let line = raw
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\[(cal:[a-z0-9-]+#[a-z0-9-]+)\]/g, (_, id) =>
        citationNumbers.has(id) ? `<a class="cite" href="#src-${citationNumbers.get(id)}">[${citationNumbers.get(id)}]</a>` : "");
    const bullet = line.match(/^\s*[*-]\s+(.*)$/);
    if (bullet) {
      if (!inList) { html.push("<ul>"); inList = true; }
      html.push(`<li>${bullet[1]}</li>`);
      continue;
    }
    if (inList) { html.push("</ul>"); inList = false; }
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    if (heading) html.push(`<h4>${heading[1]}</h4>`);
    else if (line.trim()) html.push(`<p>${line}</p>`);
  }
  if (inList) html.push("</ul>");
  return html.join("");
}

function addMessage(kind, html) {
  const div = document.createElement("div");
  div.className = `msg ${kind}`;
  div.innerHTML = html;
  $("log").appendChild(div);
  div.scrollIntoView({ behavior: "smooth", block: "end" });
  return div;
}

function renderAnswer(data) {
  const numbers = new Map(data.citations.map((c, i) => [c.chunk_id, i + 1]));
  let html = renderMarkdown(data.answer || "(no answer)", numbers);
  if (data.citations.length) {
    html += `<div class="sources">Sources<ol>${data.citations
      .map((c, i) => {
        const href = c.url.startsWith("https://") ? escapeHtml(c.url) : "#";
        return `<li id="src-${i + 1}"><a href="${href}" target="_blank" rel="noopener">${escapeHtml(c.heading)}</a></li>`;
      })
      .join("")}</ol></div>`;
  }
  const seconds = (data.metrics.latency_ms / 1000).toFixed(1);
  html += `<div class="meta">${seconds} s · ${data.metrics.model_calls} model calls</div>`;
  return html;
}

async function ask(message) {
  addMessage("user", escapeHtml(message));
  const pending = addMessage("bot thinking", "Checking the calendar…");
  setBusy(true);
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.idToken}` },
      body: JSON.stringify({ message, session_id: state.sessionId }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    state.sessionId = data.session_id;
    pending.className = "msg bot";
    pending.innerHTML = renderAnswer(data);
    pending.dataset.sessionId = data.session_id;
    window.dispatchEvent(new CustomEvent("waypoint:answer", { detail: { element: pending, data, message } }));
  } catch (err) {
    pending.className = "msg error";
    pending.textContent = err.message;
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  const signedIn = Boolean(state.idToken);
  $("send").disabled = busy || !signedIn;
  $("message").disabled = busy || !signedIn;
  document.querySelectorAll(".example").forEach((b) => (b.disabled = busy || !signedIn));
}

function onSignIn(response) {
  state.idToken = response.credential;
  const payload = JSON.parse(atob(response.credential.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  $("signin").hidden = true;
  $("who").hidden = false;
  $("who").textContent = `Signed in as ${payload.given_name || payload.email}`;
  $("message").placeholder = "Ask about programs, courses, prerequisites or a plan…";
  setBusy(false);
}

async function init() {
  const config = await (await fetch("/api/config")).json();
  state.advisorUrl = config.advisor_url;
  $("advisor-link").href = config.advisor_url;
  if (!config.google_client_id) {
    $("signin").textContent = "Sign-in isn't configured on this server.";
    return;
  }
  const start = () => {
    google.accounts.id.initialize({ client_id: config.google_client_id, callback: onSignIn });
    google.accounts.id.renderButton($("signin"), { theme: "outline", size: "large", text: "signin_with" });
  };
  if (window.google?.accounts?.id) start();
  else window.addEventListener("load", start);
}

$("ask").addEventListener("submit", (e) => {
  e.preventDefault();
  const message = $("message").value.trim();
  if (!message) return;
  $("message").value = "";
  ask(message);
});
$("message").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("ask").requestSubmit(); }
});
document.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));
init();
