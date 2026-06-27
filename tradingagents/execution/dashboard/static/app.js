"use strict";

// Static sub-labels nodding to the debate structure behind certain stages.
const SUBLABEL = {
  "Research Manager": "bull ⇄ bear → manager",
  "Portfolio Manager": "risk debate → final",
};

const $ = (id) => document.getElementById(id);
const fmtMoney = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 }));

let source = null;

function setStatus(text, live) {
  const el = $("status");
  el.textContent = text;
  el.classList.toggle("live", !!live);
}

function buildRelay(stages) {
  const relay = $("relay");
  relay.innerHTML = "";
  stages.forEach((name) => {
    const li = document.createElement("li");
    li.className = "station";
    li.dataset.stage = name;
    const sub = SUBLABEL[name] ? `<span class="sub">${SUBLABEL[name]}</span>` : "";
    li.innerHTML = `<span class="dot"></span><span class="name">${name}</span>${sub}`;
    relay.appendChild(li);
  });
  markActive(stages[0]);
}

function markActive(name) {
  document.querySelectorAll(".station").forEach((s) => {
    if (!s.classList.contains("done")) s.classList.remove("active");
  });
  const el = document.querySelector(`.station[data-stage="${cssEscape(name)}"]`);
  if (el && !el.classList.contains("done")) el.classList.add("active");
}

function markDone(name) {
  const stations = [...document.querySelectorAll(".station")];
  const idx = stations.findIndex((s) => s.dataset.stage === name);
  if (idx < 0) return;
  stations[idx].classList.remove("active");
  stations[idx].classList.add("done");
  const next = stations.slice(idx + 1).find((s) => !s.classList.contains("done"));
  if (next) markActive(next.dataset.stage);
}

function addReasoning(stage, text, index) {
  const stream = $("stream");
  const first = stream.querySelector(".empty");
  if (first) first.remove();
  const card = document.createElement("div");
  card.className = "rcard";
  const head = document.createElement("div");
  head.className = "rcard-head";
  head.innerHTML = `<span>${stage}</span><span class="ix">${String(index).padStart(2, "0")}</span>`;
  const pre = document.createElement("pre");
  pre.textContent = text; // textContent => no HTML injection
  card.appendChild(head);
  card.appendChild(pre);
  stream.appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "end" });
}

const RATING_CLASS = {
  Buy: "rating-long", Overweight: "rating-long",
  Sell: "rating-short", Underweight: "rating-short",
  Hold: "rating-flat",
};

function showDecision(rating, text) {
  const chip = $("rating");
  chip.textContent = rating || "—";
  chip.className = "rating-chip " + (RATING_CLASS[rating] || "rating-flat");
  $("decision-text").textContent = text || "";
}

function showOrder(ev) {
  const el = $("order");
  if (ev.type === "no_order") {
    el.textContent = "No order — " + ev.reason;
    el.className = "order";
    return;
  }
  const dir = ev.side === "buy" ? "long" : "short";
  el.innerHTML = `<span class="${dir}">${ev.side.toUpperCase()} ${ev.qty} ${ev.symbol}</span> · order ${ev.order_id}`;
  el.className = "order filled";
}

function handle(ev) {
  switch (ev.type) {
    case "run_started":
      $("stream").innerHTML = '<p class="empty">Waiting for the first specialist…</p>';
      showDecision(null, "");
      $("order").textContent = "No order yet.";
      $("order").className = "order";
      buildRelay(ev.stages || []);
      setStatus(`Running ${ev.ticker} — specialists are working…`, true);
      break;
    case "stage_complete":
      handle._n = (handle._n || 0) + 1;
      markDone(ev.stage);
      addReasoning(ev.stage, ev.text, handle._n);
      setStatus(`${ev.stage} done — handing off…`, true);
      break;
    case "decision":
      showDecision(ev.rating, ev.text);
      setStatus(`Decision: ${ev.rating}`, true);
      break;
    case "order":
      showOrder(ev);
      refreshPortfolio();
      break;
    case "no_order":
      showOrder(ev);
      break;
    case "done":
      setStatus("Run complete.", false);
      finish();
      refreshPortfolio();
      break;
  }
}

function finish() {
  if (source) { source.close(); source = null; }
  $("run-btn").disabled = false;
  handle._n = 0;
}

function start(ticker) {
  if (source) source.close();
  handle._n = 0;
  $("run-btn").disabled = true;
  setStatus(`Starting ${ticker}…`, true);
  source = new EventSource(`/stream/${encodeURIComponent(ticker)}`);
  source.onmessage = (e) => {
    try { handle(JSON.parse(e.data)); } catch (_) { /* ignore keep-alives */ }
  };
  source.onerror = () => { setStatus("Stream closed.", false); finish(); };
}

async function refreshPortfolio() {
  try {
    const r = await fetch("/api/portfolio");
    const v = await r.json();
    $("equity").textContent = fmtMoney(v.equity);
    $("paper-pill").style.display = v.is_paper === false ? "none" : "";

    const pos = $("positions");
    if (v.positions && v.positions.length) {
      pos.innerHTML = v.positions.map((p) => {
        const cls = p.qty >= 0 ? "long" : "short";
        return `<tr><td>${p.symbol}</td><td class="num ${cls}">${p.qty > 0 ? "+" : ""}${p.qty}</td><td class="num">${fmtMoney(p.market_value)}</td></tr>`;
      }).join("");
    } else { pos.innerHTML = '<tr><td class="muted">flat</td></tr>'; }

    const ord = $("orders");
    if (v.recent_orders && v.recent_orders.length) {
      ord.innerHTML = v.recent_orders.slice().reverse().map((o) => {
        const cls = o.side === "buy" ? "long" : "short";
        return `<tr><td class="${cls}">${o.side}</td><td>${o.symbol}</td><td class="num">${o.qty}</td></tr>`;
      }).join("");
    } else { ord.innerHTML = '<tr><td class="muted">none</td></tr>'; }
  } catch (_) { /* portfolio panel is best-effort */ }
}

// CSS.escape fallback for older engines
function cssEscape(s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/"/g, '\\"'); }

document.getElementById("run-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const t = $("ticker").value.trim().toUpperCase();
  if (t) start(t);
});

refreshPortfolio();
setInterval(refreshPortfolio, 5000);
