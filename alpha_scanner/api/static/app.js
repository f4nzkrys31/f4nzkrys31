"use strict";
// All token-provided strings are untrusted: build DOM with textContent only, never innerHTML.
const TOKEN = document.querySelector('meta[name="alpha-token"]').content;
const DEMO = document.querySelector('meta[name="alpha-demo"]').content === "1";
const CHAINS = { CT_501: "Solana", "56": "BSC", "8453": "Base" };
const EXPLORER = { CT_501: "https://solscan.io/token/", "56": "https://bscscan.com/token/", "8453": "https://basescan.org/token/" };
let activeTab = "candidates";

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "style") Object.assign(e.style, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
const api = async (path, opts = {}) => {
  const r = await fetch(path, { ...opts, headers: { "content-type": "application/json", "x-alpha-token": TOKEN, ...(opts.headers || {}) } });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
};
const usd = (v) => v === null || v === undefined ? "?" : v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `$${(v / 1e3).toFixed(1)}k` : `$${v.toFixed(0)}`;
const pct = (v) => v === null || v === undefined ? "?" : `${(v * 100).toFixed(0)}%`;
const num = (v, d = 1) => v === null || v === undefined ? "?" : Number(v).toFixed(d);
const age = (m) => m === null || m === undefined ? "?" : m < 60 ? `${m.toFixed(0)}m` : m < 1440 ? `${(m / 60).toFixed(1)}h` : `${(m / 1440).toFixed(1)}d`;
const when = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString() : "?";
const shortAddr = (a) => a.length > 14 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
function scoreColor(v, invert = false) {
  const x = Math.max(0, Math.min(100, v)) / 100, t = invert ? 1 - x : x;
  return `hsl(${Math.round(t * 130)}, 55%, ${28 + t * 8}%)`;
}
const scoreCell = (v, invert = false) => el("span", { class: "score", style: { background: scoreColor(v, invert) } }, num(v, 0));
function secBadge(s) {
  const ok = s === "checked";
  return el("span", { class: `badge ${ok ? "checked" : "unverified"}` }, ok ? "audited" : "UNVERIFIED");
}
const flagBadges = (flags) => (flags || []).slice(0, 4).map((f) => el("span", { class: `badge ${f.severity}`, title: f.message }, f.code));

// ---------- candidates ----------
async function loadCandidates() {
  const f = new FormData(document.getElementById("filters"));
  const q = new URLSearchParams();
  for (const [k, v] of f.entries()) if (v !== "" && k !== "include_blocked") q.set(k, v);
  if (f.get("include_blocked")) q.set("include_blocked", "true");
  const rows = await api(`/api/candidates?${q}`);
  const tb = document.querySelector("#cand-table tbody");
  tb.replaceChildren(...rows.map((c) => el("tr", { class: c.blocked ? "blocked" : "", onclick: () => openDetail(c.chain, c.address) },
    el("td", {}, el("div", { class: "sym" }, c.symbol || "?"), el("div", { class: "addr" }, `${CHAINS[c.chain] || c.chain} · ${shortAddr(c.address)}`)),
    el("td", {}, c.platform || "?"), el("td", {}, c.stage),
    el("td", {}, scoreCell(c.rank_score)), el("td", {}, scoreCell(c.opportunity_score)), el("td", {}, scoreCell(c.risk_score, true)),
    el("td", { class: "num" }, pct(c.confidence)), el("td", {}, secBadge(c.security_status)),
    el("td", { class: "num" }, usd(c.market_cap_usd)), el("td", { class: "num" }, usd(c.liquidity_usd)), el("td", { class: "num" }, usd(c.volume_24h_usd)),
    el("td", { class: "num" }, c.holders ?? "?"), el("td", { class: "num" }, c.volume_accel === null ? "?" : `${num(c.volume_accel, 2)}x`),
    el("td", { class: "num" }, pct(c.buy_ratio)), el("td", { class: "num" }, age(c.age_min)), el("td", {}, flagBadges(c.flags)))));
  if (!rows.length) tb.replaceChildren(el("tr", {}, el("td", { colspan: 16 }, "No candidates match (or first scan still running).")));
}

// ---------- detail ----------
function spark(series) {
  const pts = series.filter((s) => s.rank_score !== null);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "spark"); svg.setAttribute("viewBox", "0 0 300 70"); svg.setAttribute("preserveAspectRatio", "none");
  if (pts.length < 2) return el("div", { class: "note" }, "Score history appears after a few scan cycles.");
  const t0 = pts[0].ts, t1 = pts[pts.length - 1].ts || t0 + 1;
  const line = (key, color) => {
    const p = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    p.setAttribute("points", pts.map((s) => `${((s.ts - t0) / (t1 - t0 || 1)) * 300},${70 - (s[key] / 100) * 66 - 2}`).join(" "));
    p.setAttribute("fill", "none"); p.setAttribute("stroke", color); p.setAttribute("stroke-width", "2");
    svg.append(p);
  };
  line("rank_score", "#5aa2ff"); line("risk_score", "#e0564d");
  return svg;
}
async function openDetail(chain, address) {
  const d = await api(`/api/token/${chain}/${encodeURIComponent(address)}`);
  const c = d.candidate, s = c.snapshot, f = c.features;
  const body = document.getElementById("detail-body");
  const kv = (pairs) => el("div", { class: "kv" }, pairs.map(([k, v]) => el("div", {}, el("b", {}, k), v ?? "?")));
  const list = (items) => el("ul", {}, items.map((x) => el("li", {}, x)));
  const watchBtn = el("button", { class: "btn", onclick: async () => {
    if (d.watched) await api(`/api/watchlist/${chain}/${encodeURIComponent(address)}`, { method: "DELETE" });
    else await api("/api/watchlist", { method: "POST", body: JSON.stringify({ chain, address, note: "" }) });
    openDetail(chain, address);
  } }, d.watched ? "Remove from watchlist" : "Add to watchlist");
  const link = EXPLORER[chain] ? el("a", { href: EXPLORER[chain] + encodeURIComponent(address), target: "_blank", rel: "noopener noreferrer" }, "explorer ↗") : null;
  const sec = d.security;
  body.replaceChildren(
    el("h2", {}, `${c.symbol || "?"} `, el("small", {}, `${c.name || ""} · ${c.platform || "?"} · ${c.stage} · ${CHAINS[chain] || chain}`)),
    el("div", { class: "addr" }, address, " ", link),
    el("p", {}, watchBtn, c.blocked ? el("span", { class: "badge critical" }, " BLOCKED — fails a hard safety rule") : null),
    kv([["Rank", num(c.rank_score, 0)], ["Opportunity", num(c.opportunity_score, 0)], ["Risk", num(c.risk_score, 0)],
        ["Confidence", pct(c.confidence)], ["Security", sec ? sec.status : "not checked"], ["Updated", when(c.updated_at)]]),
    el("h2", {}, "Why it was detected"), list(c.why_detected.length ? c.why_detected : ["No strong positive signal"]),
    el("h2", {}, "Score components ", el("small", {}, "(weight · normalised value · evidence)")),
    ...c.components.map((k) => el("div", { class: "comp" }, el("span", {}, k.name.replace(/_/g, " ")), el("span", {}, `w${k.weight}`),
      el("div", {}, el("div", { class: "bar" }, el("span", { style: { width: `${(k.value || 0) * 100}%`, background: k.value === null ? "#444" : "" } })),
        el("div", { class: "note" }, k.value === null ? `missing — ${k.evidence}` : k.evidence)))),
    el("h2", {}, "Score history (24h) ", el("small", {}, "blue rank · red risk")), spark(d.score_history),
    el("h2", {}, "Market"), kv([["Price", s.price_usd ? `$${Number(s.price_usd).toPrecision(4)}` : "?"], ["Market cap", usd(s.market_cap_usd)],
      ["Liquidity", usd(s.liquidity_usd)], ["Liq / mcap", f.liq_to_mcap === null ? "?" : `${(f.liq_to_mcap * 100).toFixed(1)}%`],
      ["Vol 24h", usd(s.volume_24h_usd)], ["Vol 5m / 1h", `${usd(s.volume_5m_usd)} / ${usd(s.volume_1h_usd)}`],
      ["Vol accel", f.volume_accel === null ? "?" : `${num(f.volume_accel, 2)}x`], ["Buy ratio", `${pct(f.buy_ratio)} (${f.buy_ratio_window || "?"})`],
      ["Bonding progress", s.progress_pct === null ? "?" : `${num(s.progress_pct, 1)}%`], ["Age", age(f.age_min)]]),
    el("h2", {}, "Holders & wallets"), kv([["Holders", s.holders], ["Growth/min", num(f.holder_growth_per_min, 2)],
      ["Unique traders 5m", f.unique_traders_5m], ["Trades/trader 5m", f.trades_per_trader_5m],
      ["Top-10 %", num(s.top10_pct)], ["Dev %", num(s.dev_pct)], ["Snipers %", num(s.sniper_pct)], ["Insiders %", num(s.insider_pct)],
      ["Bundlers %", num(s.bundler_pct)], ["Fresh wallets %", num(s.new_wallet_pct)], ["Smart-money holders", s.smart_money_holders],
      ["KOL holders", s.kol_holders], ["Dev sold %", num(s.dev_sell_pct, 0)], ["Dev prior migrations", s.dev_migrate_count],
      ["Dev address", s.dev_address ? shortAddr(s.dev_address) : "?"]]),
    el("h2", {}, "Smart-money signals"), d.smart_money.length ? list(d.smart_money.map((m) =>
      `${m.direction.toUpperCase()} · ${m.smart_money_count ?? "?"} wallets · trigger ${when(m.trigger_time)} · max gain ${num(m.max_gain_pct, 0)}% · exit ${num(m.exit_rate_pct, 0)}% · ${m.status}`))
      : el("div", { class: "note" }, "None recorded."),
    el("h2", {}, "Security audit"), sec && sec.status === "checked"
      ? el("div", {}, kv([["Risk level", `${sec.risk_level}/5 ${sec.risk_enum || ""}`], ["Buy tax", sec.buy_tax_pct ?? "unknown"], ["Sell tax", sec.sell_tax_pct ?? "unknown"], ["Verified", String(sec.verified)]]),
          list(sec.hits.length ? sec.hits.map((h) => `${h.severity}: ${h.category} — ${h.title}`) : ["No risk items hit (point-in-time; not a guarantee)"]))
      : el("div", { class: "badge unverified" }, `UNVERIFIED — ${sec ? sec.status : "not checked yet"}. Treat contract safety as unknown.`),
    el("h2", {}, "Flags"), c.flags.length ? el("div", {}, c.flags.map((x) => el("div", {}, el("span", { class: `badge ${x.severity}` }, x.code), " ", x.message))) : el("div", { class: "note" }, "None."),
    el("h2", {}, "What would invalidate the thesis"), list(c.invalidation),
    el("h2", {}, "Remaining risks"), list(c.remaining_risks),
  );
  document.getElementById("detail").classList.remove("hidden");
}
document.getElementById("detail-close").onclick = () => document.getElementById("detail").classList.add("hidden");

// ---------- feed / heatmap / alerts / watchlist / health ----------
async function loadFeed() {
  const rows = await api("/api/feed?limit=150");
  document.querySelector("#feed-table tbody").replaceChildren(...rows.map((r) => el("tr", { onclick: () => openDetail(r.chain, r.address) },
    el("td", {}, when(r.first_seen)), el("td", {}, el("span", { class: "sym" }, r.symbol || "?"), " ", el("span", { class: "addr" }, `${CHAINS[r.chain] || r.chain} · ${shortAddr(r.address)}`)),
    el("td", {}, r.platform || "?"), el("td", {}, r.candidate ? scoreCell(r.candidate.rank_score) : "?"),
    el("td", {}, r.candidate ? scoreCell(r.candidate.risk_score, true) : "?"), el("td", { class: "num" }, usd(r.candidate?.liquidity_usd)),
    el("td", { class: "num" }, r.candidate?.holders ?? "?"))));
}
async function loadHeatmap() {
  const h = await api("/api/heatmap");
  const L = h.launchpad, box = document.getElementById("heat-launch");
  box.style.gridTemplateColumns = `140px repeat(${L.columns.length}, minmax(110px, 1fr))`;
  const cells = [el("div", { class: "hdr" }, "platform \\ age"), ...L.columns.map((c) => el("div", { class: "hdr" }, c))];
  for (const row of L.rows) {
    cells.push(el("div", { class: "hdr" }, row.platform));
    for (const c of row.cells) {
      if (!c) { cells.push(el("div", { class: "cell" })); continue; }
      const cell = el("div", { class: "cell", style: { background: scoreColor(c.mean_rank * 2) }, title: `${c.blocked} blocked` },
        el("b", {}, `${c.n} tokens`), el("div", {}, `mean rank ${c.mean_rank}`), el("div", {}, `vol ${usd(c.volume_24h_usd)}`),
        c.best ? el("div", { class: "addr" }, `best: ${c.best.symbol || "?"} (${num(c.best.rank_score, 0)})`) : null);
      if (c.best) { cell.style.cursor = "pointer"; cell.onclick = () => openDetail(c.best.chain, c.best.address); }
      cells.push(cell);
    }
  }
  box.replaceChildren(...cells);
  const maxN = Math.max(1, ...h.narratives.map((n) => Math.abs(n.net_inflow_1h_usd || 0)));
  document.getElementById("heat-narr").replaceChildren(...(h.narratives.length ? h.narratives.map((n) => el("div", { class: "tile",
    style: { background: scoreColor(50 + 50 * (n.net_inflow_1h_usd || 0) / maxN) } }, el("b", {}, n.name), el("div", {}, `${CHAINS[n.chain] || n.chain} · ${usd(n.net_inflow_1h_usd)} 1h · ${n.tokens} tokens`)))
    : [el("div", { class: "note" }, "No narrative data yet.")]));
  const maxV = Math.max(1, ...h.spot.map((s) => s.quote_volume));
  document.getElementById("heat-spot").replaceChildren(...(h.spot.length ? h.spot.map((s) => el("div", { class: "tile",
    style: { background: scoreColor(50 + s.change_pct * 5), minWidth: `${60 + 80 * Math.sqrt(s.quote_volume / maxV)}px` } },
    el("b", {}, s.symbol), el("div", {}, `${s.change_pct > 0 ? "+" : ""}${num(s.change_pct, 1)}%`), el("div", { class: "addr" }, usd(s.quote_volume))))
    : [el("div", { class: "note" }, "Spot data unavailable.")]));
}
async function loadAlerts() {
  const rows = await api("/api/alerts?limit=100");
  // Alert text is stored as Telegram HTML; show it as plain text with tags stripped by the browser parser in an inert document.
  const strip = (h) => new DOMParser().parseFromString(h, "text/html").body.textContent;
  document.getElementById("alerts-list").replaceChildren(...(rows.length ? rows.map((a) => el("div", { class: "alert", onclick: () => openDetail(a.chain, a.address) },
    el("div", { class: "addr" }, `${new Date(a.ts * 1000).toLocaleString()} · ${a.kind} · delivered: ${Object.keys(a.delivered || {}).filter((k) => a.delivered[k]).join(", ")}`),
    strip(a.message || ""))) : [el("div", { class: "note" }, "No alerts yet.")]));
}
async function loadWatchlist() {
  const rows = await api("/api/watchlist");
  document.querySelector("#watch-table tbody").replaceChildren(...rows.map((r) => el("tr", {},
    el("td", { onclick: () => openDetail(r.chain, r.address) }, el("span", { class: "sym" }, r.candidate?.symbol || "?"), " ", el("span", { class: "addr" }, shortAddr(r.address))),
    el("td", {}, r.note || ""), el("td", {}, r.candidate ? scoreCell(r.candidate.rank_score) : "?"), el("td", {}, r.candidate ? scoreCell(r.candidate.risk_score, true) : "?"),
    el("td", {}, when(r.added_at)),
    el("td", {}, el("button", { class: "btn", onclick: async () => { await api(`/api/watchlist/${r.chain}/${encodeURIComponent(r.address)}`, { method: "DELETE" }); loadWatchlist(); } }, "remove")))));
}
async function loadHealth() {
  const h = await api("/api/health");
  const pill = document.getElementById("health-pill");
  const openC = h.sources.filter((s) => s.circuit_open).length;
  pill.textContent = h.stale ? "data stale" : `ok · cycle ${h.scanner.cycles ?? "?"} · ${h.scanner.last_tokens ?? 0} tokens${openC ? ` · ${openC} source(s) down` : ""}`;
  pill.className = `pill ${h.stale || openC ? "bad" : "ok"}`;
  if (activeTab === "health") document.getElementById("health-json").textContent = JSON.stringify(h, null, 2);
}

const loaders = { candidates: loadCandidates, feed: loadFeed, heatmap: loadHeatmap, alerts: loadAlerts, watchlist: loadWatchlist, health: loadHealth };
async function refresh() {
  try { await Promise.all([loaders[activeTab](), loadHealth()]); } catch (e) { document.getElementById("health-pill").textContent = `error: ${e.message}`; }
}
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => {
  activeTab = b.dataset.tab;
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== `tab-${activeTab}`));
  refresh();
}));
document.getElementById("filters").addEventListener("change", loadCandidates);
if (DEMO) document.getElementById("demo-banner").classList.remove("hidden");
refresh();
setInterval(refresh, 15000);
