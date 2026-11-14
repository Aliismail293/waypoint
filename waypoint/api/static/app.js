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

// Reads a Server-Sent Events response and calls onEvent(name, data) for each event as it arrives.
async function readEvents(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const name = (block.match(/^event: (.*)$/m) || [])[1];
      const data = (block.match(/^data: (.*)$/m) || [])[1];
      if (name && data) onEvent(name, JSON.parse(data));
    }
  }
}

// Streamed text may end mid-citation ("[cal:abc#d"): hide citation markers until the final text swaps them for numbers.
function stripCitations(text) {
  return text.replace(/\[cal:[^\]]*\]?/g, "").replace(/\[c(?:a(?:l:?)?)?$/, "");
}

async function ask(message) {
  addMessage("user", escapeHtml(message));
  const pending = addMessage("bot", '<ul class="steps"></ul><div class="streamed"></div>');
  const steps = pending.querySelector(".steps");
  const streamed = pending.querySelector(".streamed");
  const stepItems = new Map();
  let text = "";
  setBusy(true);
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.idToken}` },
      body: JSON.stringify({ message, session_id: state.sessionId }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Request failed (${response.status})`);
    }
    let finalData = null;
    await readEvents(response, (name, data) => {
      if (name === "status") {
        let item = stepItems.get(data.id || data.step);
        if (!item) {
          item = document.createElement("li");
          steps.appendChild(item);
          stepItems.set(data.id || data.step, item);
        }
        item.className = data.state;
        item.textContent = (data.state === "done" ? "✓ " : "… ") + data.label + (data.ms ? ` (${(data.ms / 1000).toFixed(1)} s)` : "");
      } else if (name === "token") {
        text += data.text;
        streamed.innerHTML = renderMarkdown(stripCitations(text), new Map());
      } else if (name === "final") {
        finalData = data;
      } else if (name === "error") {
        throw new Error(data.message);
      }
    });
    if (!finalData) throw new Error("The answer stream ended early. Please try again.");
    const data = { answer: finalData.answer, citations: finalData.citations, session_id: finalData.session_id, answer_id: finalData.answer_id, metrics: finalData.metrics };
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

function postJson(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.idToken}` },
    body: JSON.stringify(body),
  });
}

// Under every answer: thumbs up/down and "Ask an advisor". Only ids and the rating are sent, never text.
window.addEventListener("waypoint:answer", (event) => {
  const { element, data } = event.detail;
  const ids = { session_id: data.session_id, answer_id: data.answer_id };
  const bar = document.createElement("div");
  bar.className = "feedback";
  bar.innerHTML =
    '<span>Helpful?</span>' +
    '<button type="button" data-rating="1" aria-label="Helpful">👍</button>' +
    '<button type="button" data-rating="-1" aria-label="Not helpful">👎</button>' +
    `<a class="escalate" href="${escapeHtml(state.advisorUrl)}" target="_blank" rel="noopener">Ask an advisor</a>`;
  bar.querySelectorAll("button").forEach((button) =>
    button.addEventListener("click", async () => {
      bar.querySelectorAll("button").forEach((b) => b.classList.remove("chosen"));
      button.classList.add("chosen");
      await postJson("/api/feedback", { ...ids, rating: Number(button.dataset.rating) }).catch(() => {});
    }),
  );
  // The link opens normally; the escalation is recorded alongside (it must not block the navigation).
  bar.querySelector(".escalate").addEventListener("click", () => {
    postJson("/api/escalate", ids).catch(() => {});
  });
  element.appendChild(bar);
});

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
