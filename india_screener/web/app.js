"use strict";
const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const inr = (x, d = 2) => x == null ? "–" : "₹" + Number(x).toLocaleString("en-IN", {minimumFractionDigits: d, maximumFractionDigits: d});
const cr = x => x == null ? "–" : "₹" + Math.round(x).toLocaleString("en-IN") + " cr";
const pct = (x, d = 1) => x == null ? "–" : (x * 100).toFixed(d) + "%";
const signed = (x, f = inr) => x == null ? "–" : `<span class="${x >= 0 ? "gain" : "loss"}">${x >= 0 ? "+" : "−"}${f(Math.abs(x))}</span>`;
const today = () => new Date(Date.now() + 5.5 * 3600e3).toISOString().slice(0, 10);

function status(msg, err = false, ms = 4000) {
  const el = $("#status"); el.textContent = msg; el.className = err ? "err" : ""; el.style.display = "block";
  clearTimeout(status.t); status.t = setTimeout(() => el.style.display = "none", ms);
}
async function api(path, body, raw = false) {
  const opt = body === undefined ? {} : {method: "POST", headers: {"X-Screener": "1"}, body: raw ? body : JSON.stringify(body)};
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({error: "unreadable response"}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
async function guard(fn) { try { return await fn(); } catch (e) { status(e.message, true, 8000); } }

// ---- stock search shared by every picker
let searchTimer;
document.addEventListener("input", e => {
  if (!e.target.matches("input[list=stocklist]")) return;
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  searchTimer = setTimeout(async () => {
    if (!q) return;
    const rows = await api("/api/universe?q=" + encodeURIComponent(q)).catch(() => []);
    $("#stocklist").innerHTML = rows.map(r => `<option value="${esc(r.symbol)}">${esc(r.name)}</option>`).join("");
  }, 180);
});
const symOf = v => (v || "").trim().toUpperCase().split(/\s/)[0];

// ---- appearance: theme, text size, sidebar (remembered in this browser only)
const ui = (() => { try { return JSON.parse(localStorage.getItem("screener-ui") || "{}"); } catch (e) { return {}; } })();
function saveUi() { try { localStorage.setItem("screener-ui", JSON.stringify(ui)); } catch (e) {} }
const root = document.documentElement;
// Apply saved choices (an inline script in index.html would be blocked by the page's security policy).
if (ui.theme) root.dataset.theme = ui.theme;
if (ui.text) root.dataset.text = ui.text;
if (ui.navCollapsed) root.dataset.nav = "collapsed";
$("#themesel").value = ui.theme || "light";
$("#textsel").value = ui.text || "";
$("#themesel").onchange = e => { ui.theme = e.target.value; root.dataset.theme = ui.theme; saveUi(); };
$("#textsel").onchange = e => { ui.text = e.target.value; if (ui.text) root.dataset.text = ui.text; else delete root.dataset.text; saveUi(); };
$("#navtoggle").onclick = () => { ui.navCollapsed = !ui.navCollapsed;
  if (ui.navCollapsed) root.dataset.nav = "collapsed"; else delete root.dataset.nav; saveUi(); };
$("#bell").onclick = () => go("notifications");
$("#helpbtn").onclick = () => go("help");
async function refreshUnread() {
  const d = await api("/api/unread").catch(() => null);
  if (d) $("#unread").textContent = d.unread ? d.unread : "";
}
setInterval(refreshUnread, 60000);

// ---- navigation
const views = {portfolio, analyse, top250, scores, alerts, ledger, notifications, settings, help};
function go(view, arg) {
  const h = view + (arg ? "/" + arg : "");
  if (location.hash.slice(1) !== h) location.hash = h; else route();
}
function route() {
  let [view, arg] = (location.hash.slice(1) || "portfolio").split("/");
  if (!views[view]) view = "portfolio";
  document.querySelectorAll("nav button[data-view]").forEach(b =>
    b.dataset.view === view ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current"));
  main.innerHTML = '<p class="muted">Loading…</p>';
  views[view](arg ? decodeURIComponent(arg) : undefined); main.focus();
}
window.addEventListener("hashchange", route);
document.querySelectorAll("nav button[data-view]").forEach(b => b.onclick = () => go(b.dataset.view));

// ---- portfolio
async function portfolio() {
  const d = await guard(() => api("/api/portfolio")); if (!d) return;
  const open = d.positions.filter(p => p.quantity > 0), closed = d.positions.filter(p => !p.quantity);
  main.innerHTML = `
  <h2>My portfolio</h2>
  <p class="lead">Add each purchase once. Values use the last completed daily close, so they settle after 4 pm.</p>
  <section class="box"><h3 style="margin-top:0">Add a transaction</h3>
    <form class="row" id="txn">
      <label>Stock<input name="symbol" list="stocklist" placeholder="Type a name or symbol" required size="18" autocomplete="off"></label>
      <label>Type<select name="kind"><option>BUY</option><option>SELL</option><option>DIVIDEND</option><option>SPLIT</option><option>BONUS</option></select></label>
      <label>Date<input name="date" type="date" value="${today()}" required></label>
      <label class="f-trade">Quantity<input name="quantity" type="number" min="1" step="1"></label>
      <label class="f-trade">Price per share (₹)<input name="price" type="number" min="0" step="0.01"></label>
      <label class="f-trade">Charges (₹)<input name="fees" type="number" min="0" step="0.01" value="0"></label>
      <label class="f-div" hidden>Total dividend received (₹)<input name="amount" type="number" min="0" step="0.01"></label>
      <label class="f-ratio" hidden>Ratio (new:old or bonus:held)<input name="ratio" placeholder="5:1" size="7"></label>
      <label>Exchange<select name="exchange"><option>NSE</option><option>BSE</option></select></label>
      <button class="btn">Save transaction</button>
    </form>
    <div class="row" style="margin-top:14px"><label>Or import a Zerodha tradebook (Console → Reports → Tradebook → CSV)
      <input type="file" id="tb" accept=".csv"></label></div>
  </section>
  <section class="box scroll"><h3 style="margin-top:0">Holdings</h3>${open.length ? `
    <table><thead><tr><th class="l">Stock</th><th>Qty</th><th>Avg cost</th><th>Last close</th><th>Day</th><th>Invested</th><th>Value</th><th>Unrealised</th><th>Dividends</th></tr></thead>
    <tbody>${open.map(p => `<tr><td class="l"><button class="link" data-an="${esc(p.symbol)}">${esc(p.symbol)}</button></td>
      <td>${p.quantity}</td><td>${inr(p.avg_cost)}</td><td>${p.price_error ? '<span class="loss" title="' + esc(p.price_error) + '">no price</span>' : srcSpan(inr(p.last_close), `Yahoo Finance daily close, ${p.close_date || ""}`)}</td>
      <td>${signed(p.day_change, pct)}</td><td>${inr(p.invested, 0)}</td><td>${inr(p.value, 0)}</td>
      <td>${signed(p.unrealised, x => inr(x, 0))} ${p.unrealised != null ? '<span class="muted">(' + (p.unrealised >= 0 ? "+" : "−") + pct(Math.abs(p.unrealised / p.invested)) + ')</span>' : ""}</td>
      <td>${inr(p.dividends, 0)}</td></tr>`).join("")}</tbody>
    <tfoot><tr><td class="l">Total (priced holdings)</td><td></td><td></td><td></td><td></td><td>${inr(d.totals.invested_priced, 0)}</td><td>${inr(d.totals.value, 0)}</td>
      <td>${signed(d.totals.unrealised, x => inr(x, 0))}</td><td></td></tr></tfoot></table>` : '<p class="muted">No holdings yet. Add your first purchase above.</p>'}
    ${closed.length ? `<p class="muted">Closed positions: ${closed.map(p => `${esc(p.symbol)} (realised ${inr(p.realised, 0)})`).join(", ")}</p>` : ""}
  </section>
  <section class="box scroll"><h3 style="margin-top:0">Transactions</h3>${d.transactions.length ? `
    <table><thead><tr><th class="l">Date</th><th class="l">Stock</th><th class="l">Type</th><th>Qty</th><th>Price</th><th>Charges</th><th>Detail</th><th></th></tr></thead>
    <tbody>${d.transactions.map(t => `<tr><td class="l">${t.trade_date}</td><td class="l">${esc(t.symbol)}</td><td class="l">${t.kind}</td>
    <td>${t.quantity || ""}</td><td>${t.price ? inr(t.price) : ""}</td><td>${t.fees ? inr(t.fees) : ""}</td>
    <td>${t.kind === "DIVIDEND" ? inr(t.amount) : t.ratio_num ? t.ratio_num + ":" + t.ratio_den : esc(t.note)}</td>
    <td><button class="link" data-del="${t.id}">Delete</button></td></tr>`).join("")}</tbody></table>` : '<p class="muted">None yet.</p>'}
  </section>`;
  const f = $("#txn");
  const toggle = () => { const k = f.kind.value;
    f.querySelectorAll(".f-trade").forEach(x => x.hidden = !["BUY", "SELL"].includes(k));
    $(".f-div", f).hidden = k !== "DIVIDEND"; $(".f-ratio", f).hidden = !["SPLIT", "BONUS"].includes(k); };
  f.kind.onchange = toggle;
  f.onsubmit = e => { e.preventDefault(); guard(async () => {
    const b = Object.fromEntries(new FormData(f)); b.symbol = symOf(b.symbol);
    for (const k of ["quantity", "price", "fees", "amount", "ratio"]) if (b[k] === "") delete b[k];
    await api("/api/txn", b); status(`Saved ${b.kind} ${b.symbol}`); portfolio(); }); };
  $("#tb").onchange = e => guard(async () => { const file = e.target.files[0]; if (!file) return;
    const r = await api("/api/import/zerodha", await file.text(), true);
    status(`Imported ${r.added} trades, skipped ${r.skipped} (duplicates or non-equity)`); portfolio(); });
  main.querySelectorAll("[data-del]").forEach(b => b.onclick = () => confirm("Delete this transaction?") &&
    guard(async () => { await api("/api/txn/delete", {id: +b.dataset.del}); portfolio(); }));
  bindAnalyseLinks();
}
function bindAnalyseLinks() { main.querySelectorAll("[data-an]").forEach(b => b.onclick = () => go("analyse", b.dataset.an)); }

// ---- analyse
function spark(series) {
  if (!series || series.length < 2) return "";
  const v = series.map(x => x[1]), lo = Math.min(...v), hi = Math.max(...v), w = 600, h = 90;
  const pts = v.map((y, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(h - 4 - (y - lo) / (hi - lo || 1) * (h - 8)).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Price over the last year">
    <polyline points="${pts}" fill="none" stroke="${v.at(-1) >= v[0] ? "var(--gain)" : "var(--loss)"}" stroke-width="2"/></svg>`;
}
let critSrc = () => "";
function critRow(c) {
  const ess = c.essential_min != null ? `<span class="ess ${c.score != null && c.score < c.essential_min ? "bad" : ""}" title="${esc(c.why_essential)}">must pass</span>` : "";
  return `<tr title="${esc(c.rule + (critSrc(c) ? "\nSource: " + critSrc(c) : ""))}"><td class="l">${esc(c.label)}${ess}<br><span class="muted">${esc(c.value)} · weight ${c.weight}</span></td>
    <td>${c.score == null ? '<span class="muted">no data</span>' : Math.round(c.score * 100) + "%"}</td></tr>`;
}
function philCard(key, title, r, pass) {
  const s = r.score ?? 0, core = r.criteria.filter(c => c.kind !== "penalty"), flags = r.criteria.filter(c => c.kind === "penalty");
  const verdict = r.matches ? "Matches" : !r.reliable ? "Not enough data" : r.failed_essentials.length ? "Fails a must-pass test"
    : r.unknown_essentials.length ? "Must-pass test lacks data" : "Below pass mark";
  return `<div class="phil" style="--c:var(--${key})"><div class="row" style="justify-content:space-between;align-items:baseline">
    <strong>${title}</strong><span class="muted">${verdict}</span></div>
    <div class="big">${r.score == null ? "–" : s.toFixed(0) + "%"}</div>
    <div class="bar" title="Pass mark ${pass}%"><i style="width:${s}%"></i><span class="pass" style="left:${pass}%"></span></div>
    <div class="muted" style="font-size:.867rem">${pct(r.coverage, 0)} of the test could run${r.deductions ? ` · red flags −${r.deductions}` : ""}</div>
    ${r.failed_essentials.length ? `<div class="warn" style="font-size:.867rem">Fails: ${r.failed_essentials.map(esc).join(", ")}</div>` : ""}
    <table class="crit" style="margin-top:10px"><tbody>${core.map(critRow).join("")}</tbody></table>
    ${flags.length ? `<div class="muted" style="font-size:.867rem;margin-top:10px">Red flags (subtract up to the weight shown)</div>
    <table class="crit flags"><tbody>${flags.map(c => `<tr title="${esc(c.rule + (critSrc(c) ? "\nSource: " + critSrc(c) : ""))}"><td class="l">${esc(c.label)}<br><span class="muted">${esc(c.value)} · up to −${c.weight}</span></td>
      <td>${c.score == null ? "no data" : c.score >= 0.999 ? "clear" : "−" + ((1 - c.score) * c.weight).toFixed(1)}</td></tr>`).join("")}</tbody></table>` : ""}</div>`;
}
function checksList(checks) {
  return `<ul class="checks">${checks.map(c => `<li><span class="st-${c.status}">${{ok: "✓", warn: "!", fail: "✗", skip: "–"}[c.status]}</span>
    ${esc(c.check)}: <span class="muted">${esc(c.detail)}</span></li>`).join("")}</ul>`;
}
async function analyse(sym) {
  main.innerHTML = `<h2>Analyse a stock</h2>
    <p class="lead">Pick any NSE stock. For the Buffett, Munger and Lynch scores, import its financials from Screener.in (free account):
    open the company, switch to Consolidated if available, click Export to Excel, then upload that file here.</p>
    <form class="row" id="pick"><label>Stock<input name="s" list="stocklist" value="${esc(sym || "")}" required size="22" autocomplete="off" placeholder="e.g. Asian Paints"></label>
    <button class="btn">Show analysis</button></form><div id="res"></div>`;
  $("#pick").onsubmit = e => { e.preventDefault(); go("analyse", symOf(e.target.s.value)); };
  if (!sym) return;
  $("#res").innerHTML = '<p class="muted">Fetching prices, news and scores…</p>';
  const d = await guard(() => api("/api/stock?symbol=" + encodeURIComponent(sym)));
  if (!d) { $("#res").innerHTML = ""; return; }
  const sc = d.score, st = await api("/api/settings").catch(() => ({scoring: {pass_score: 65}}));
  const pass = st.scoring.pass_score, src = d.sources || {manual: {}};
  critSrc = c => ({asset_quality: src.manual?.gnpa_pct, no_pledge: src.manual?.pledge_pct, promoter: src.manual?.promoter_pct,
    under_owned: src.manual?.institutional_pct})[c.key] || (/price|margin_of_safety|peg|pe$|retained/.test(c.key) ? src.price + "\n" + src.financials : src.financials);
  $("#res").innerHTML = `
  <section class="box"><div class="row" style="justify-content:space-between">
    <div><h3 style="margin:0">${esc(d.name)} <span class="muted">${esc(d.symbol)}</span></h3>
    ${d.price ? `<div><span class="src" title="Yahoo Finance daily close for ${esc(d.price.date)} (chart and 52-week range). Valuations use: ${esc(src.price || "n/a")}">${inr(d.price.last)}</span> <span class="muted">close ${d.price.date} · 1y ${signed(d.price.change_1y, pct)} · 52w range ${inr(d.price.low_52w, 0)}–${inr(d.price.high_52w, 0)}</span></div>` :
      `<div class="loss">Price unavailable: ${esc(d.price_error)}</div>`}
    ${d.position ? `<div>You hold ${d.position.quantity} at ${inr(d.position.avg_cost)} average</div>` : ""}</div>
    <div class="row">${d.shortlisted ? '<button class="btn ghost" id="unsl">Remove from shortlist</button>' : '<button class="btn" id="sl">Add to shortlist</button>'}
    <a href="${esc(d.screener_url)}" target="_blank" rel="noopener">Open on Screener.in</a></div></div>
    ${d.price ? spark(d.price.series) : ""}</section>
  ${sc ? `<section>${sc.warnings.map(w => `<div class="warn">${esc(w)}</div>`).join("")}
    <p><span class="chip">${esc(sc.lynch_category)}</span><span class="chip src"${tip(src.market_cap)}>${esc(sc.size || "size n/a")} cap · ${cr(sc.market_cap_cr)}</span>
    <span class="chip src"${tip(src.pe)}>P/E ${sc.pe ? sc.pe.toFixed(1) : "–"}</span>
    ${sc.provisional ? '<span class="chip warnchip">provisional: short history</span>' : ""}<span class="chip src"${tip(src.pb)}>P/B ${sc.pb ? sc.pb.toFixed(1) : "–"}</span>
    <span class="chip src"${tip(src.financials)}>${esc(sc.basis)} · ${esc(sc.years)}</span>${sc.all_three ? '<span class="chip all">Matches all three</span>' : ""}</p>
    <p>${sc.returns.applicable ? `<span class="chip src"${tip(src.roce + "\n5-year median: " + (sc.returns.roce_5y == null ? "n/a" : pct(sc.returns.roce_5y)))}>ROCE ${sc.returns.roce == null ? "–" : pct(sc.returns.roce)}</span>
      <span class="chip src"${tip(src.roic + "\n5-year median: " + (sc.returns.roic_5y == null ? "n/a" : pct(sc.returns.roic_5y)))}>ROIC ${sc.returns.roic == null ? "–" : pct(sc.returns.roic)}</span>`
      : `<span class="chip" title="Not meaningful for banks, NBFCs and insurers: borrowed money is their raw material">ROCE / ROIC n/a</span>`}
      <span class="chip src"${tip(src.roe + "\n5-year median: " + (sc.returns.roe_5y == null ? "n/a" : pct(sc.returns.roe_5y)))}>ROE ${sc.returns.roe == null ? "–" : pct(sc.returns.roe)}</span>
      ${Object.values(src.manual || {}).map(t => `<span class="chip src"${tip(t)}>${esc(t.split(":")[0])} ✓</span>`).join("")}</p>
    ${sc.headroom ? `<p class="muted">Price trigger: ${trigger(sc.headroom)} <span style="font-size:.867rem">— ${esc(sc.headroom.note)}, all else equal.</span></p>` : ""}
    <div class="philos">${philCard("buffett", "Buffett", sc.buffett, pass)}${philCard("munger", "Munger", sc.munger, pass)}${philCard("lynch", "Lynch", sc.lynch, pass)}</div></section>`
    : d.score_error ? `<div class="warn">Scoring failed: ${esc(d.score_error)}</div>` : `<div class="warn">No financials imported yet, so no Buffett/Munger/Lynch scores.</div>`}
  ${d.checks ? `<section class="box"><h3 style="margin-top:0">Data checks</h3>${checksList(d.checks)}
    <div class="row"><label>Business type<select id="sect">${["general", "bank", "nbfc", "insurance"].map(t =>
      `<option ${d.fundamentals.sector_type === t ? "selected" : ""}>${t}</option>`).join("")}</select></label>
    <button class="btn ghost" id="vy">Compare year-end prices with Yahoo</button></div><div id="vyres"></div></section>` : ""}
  <section class="box"><h3 style="margin-top:0">${d.fundamentals ? "Update" : "Import"} financials</h3>
    <form class="row" id="imp">
      <label>Screener.in export (.xlsx) or filled CSV template<input type="file" name="file" accept=".xlsx,.csv" required></label>
      <label>Statements<select name="basis"><option value="consolidated">Consolidated</option><option value="standalone">Standalone</option></select></label>
      <label>Business type<select name="sector"><option value="general">General company</option><option value="bank">Bank</option>
        <option value="nbfc">NBFC</option><option value="insurance">Insurance</option></select></label>
      <label style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" name="force"> Import even if the company name differs</label>
      <button class="btn">Import</button></form>
    <p class="muted" style="font-size:.867rem">${d.fundamentals ? `Last imported ${esc(d.fundamentals.imported_at.slice(0, 10))} (${esc(d.fundamentals.basis)}, ${esc(d.fundamentals.sector_type)}). ` : ""}
    No Screener export? <a href="/template.csv">Download the CSV template</a> and fill it from the annual report.</p>
    ${d.fundamentals ? `<h3>Extra facts not in the export</h3><p class="muted" style="font-size:.867rem">From Screener's shareholding section or the exchange filings. Leave blank if unknown; the score shows how much of the test ran.</p>
    <form class="row" id="man">${[["promoter_pct", "Promoter holding %"], ["pledge_pct", "Promoter shares pledged %"], ["institutional_pct", "FII + DII holding %"], ["gnpa_pct", "Gross NPA % (lenders)"]]
      .map(([k, l]) => `<label${tip(src.manual?.[k])}>${l}<input type="number" step="0.01" min="0" max="100" name="${k}" value="${d.fundamentals.manual[k] ?? ""}">
        <span style="font-size:.8rem">${esc((src.manual?.[k] || "").split(": ").slice(1).join(": ") || "not set")}</span></label>`).join("")}
      <button class="btn ghost">Save facts</button></form>` : ""}
  </section>
  <section class="box"><h3 style="margin-top:0">News, last 14 days</h3>
    ${d.news_error ? `<p class="loss">News unavailable: ${esc(d.news_error)}</p>` : d.news.length ? `<ul class="news">${d.news.map(n =>
      `<li><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a> <span class="muted">${esc(n.source)}, ${esc(n.published_utc.slice(0, 10))}</span>
      ${n.categories.map(c => `<span class="chip">${esc(c)}</span>`).join("")}</li>`).join("")}</ul>` : '<p class="muted">No company-specific headlines found.</p>'}
    ${d.events.length ? `<h3>Tracked events</h3><table><tbody>${d.events.map(evRow).join("")}</tbody></table>` : ""}
  </section>`;
  const add = $("#sl"), rem = $("#unsl");
  if (add) add.onclick = () => guard(async () => { const al = prompt("Other names the news uses for it (comma separated, optional)", "") || "";
    await api("/api/shortlist/add", {symbol: d.symbol, aliases: al.split(",")}); status("Added to shortlist"); analyse(d.symbol); });
  if (rem) rem.onclick = () => guard(async () => { await api("/api/shortlist/remove", {symbol: d.symbol}); analyse(d.symbol); });
  $("#imp").onsubmit = e => { e.preventDefault(); guard(async () => { const f = e.target, file = f.file.files[0];
    const q = new URLSearchParams({symbol: d.symbol, basis: f.basis.value, sector: f.sector.value, filename: file.name, force: f.force.checked ? "1" : "0"});
    const r = await api("/api/import/financials?" + q, await file.arrayBuffer(), true);
    status(`Imported ${r.years.length} years of financials`); analyse(d.symbol); }); };
  const sect = $("#sect");
  if (sect) sect.onchange = () => guard(async () => { await api("/api/sector", {symbol: d.symbol, sector_type: sect.value});
    status("Business type changed"); analyse(d.symbol); });
  const vy = $("#vy");
  if (vy) vy.onclick = () => guard(async () => { $("#vyres").innerHTML = '<p class="muted">Fetching 10 years of prices…</p>';
    const r = await api("/api/verify-prices?symbol=" + encodeURIComponent(d.symbol)); $("#vyres").innerHTML = checksList(r.checks); });
  const man = $("#man");
  if (man) man.onsubmit = e => { e.preventDefault(); guard(async () => {
    await api("/api/manual", {symbol: d.symbol, ...Object.fromEntries(new FormData(man))}); status("Saved"); analyse(d.symbol); }); };
}
function evRow(e) {
  const dip = e.detail.dip || {}, lv = e.detail.levelling || {};
  return `<tr><td class="l">${e.event_session}</td><td class="l">${esc(e.symbol)}</td><td class="l">${e.status.replace("_", " ")}</td>
    <td>${dip.abnormal_return != null ? "abnormal " + pct(dip.abnormal_return) : ""}</td>
    <td>${lv.recovery_vs_pre_event != null ? "now " + pct(lv.recovery_vs_pre_event) + " vs before" : ""}</td></tr>`;
}

// ---- top 250
const T0 = {universe: "nifty", data: "all", bucket: "any", industry: "", category: "", size: "", minB: "", minM: "", minL: "", q: "", sort: "default", dir: -1};
let t250 = {...T0}, t250data = null;
const BUCKET_FILTERS = [
  ["any", "Any (no match filter)"], ["quality", "Quality core: Buffett + Munger"], ["growth", "Growth: matches Lynch"],
  ["buffett", "Matches Buffett"], ["munger", "Matches Munger"], ["two_plus", "Two or more"], ["all_three", "All three"],
  ["any_match", "At least one"], ["none", "No match"], ["needs_input", "Needs your input to decide (e.g. NPA)"]];
const PRESETS = [["quality", "Quality core"], ["growth", "Growth (Lynch)"], ["two_plus", "Two or more"], ["all_three", "All three"]];
const has = (r, k) => (r.matches || []).includes(k);
// How many of the collapsed "More filters" differ from their defaults (shown on the toggle so a hidden filter is never a surprise).
const activeFilters = () => ["universe", "q", "bucket", "data", "category", "size", "minB", "minM", "minL"]
  .filter(k => t250[k] !== T0[k] && !(k === "bucket" && PRESETS.some(([v]) => v === t250.bucket))).length;
// Hover text saying where a figure came from. `s` is the row's `src` object from the server.
const tip = t => t ? ` title="${esc(t)}"` : "";
const srcSpan = (html, t) => t ? `<span class="src"${tip(t)}>${html}</span>` : html;
// ROCE / ROIC cell: latest year, with the 5-year median and the source on hover. Not meaningful for lenders/insurers.
function retCell(r, k) {
  const x = r.returns;
  if (!x) return '<span class="muted">–</span>';
  if (!x.applicable) return `<span class="muted"${tip(`${k.toUpperCase()} isn't meaningful for banks, NBFCs and insurers: borrowed money is their raw material. `
    + `Use ROE (${x.roe == null ? "n/a" : pct(x.roe)}) instead.`)}>n/a</span>`;
  if (x[k] == null) return '<span class="muted">–</span>';
  return srcSpan(pct(x[k]), `${r.src?.[k] || ""}\n5-year median: ${x[k + "_5y"] == null ? "n/a" : pct(x[k + "_5y"])}`);
}
// Price trigger: the move at which the Buy/Sell picture flips, all else equal.
function trigger(h) {
  if (!h) return '<span class="muted">–</span>';
  const each = Object.entries(h.each || {}).map(([k, e]) => `${k[0].toUpperCase() + k.slice(1)}: ` + (e.pct == null
    ? (e.direction === "up" ? "keeps matching even at 10x the price" : "doesn't match at any price within −80%")
    : (e.direction === "up" ? "stops matching at +" : "starts matching at −") + pct(Math.abs(e.pct)))).join("\n");
  const how = "\n\nWorked out by re-scoring the company at different prices with today's financials; new results or ownership changes move it.";
  if (h.pct == null) return `<span class="src muted"${tip(h.note + "\n\n" + each + how)}>${h.direction === "up" ? "not price-driven" : "quality, not price"}</span>`;
  const txt = (h.pct >= 0 ? "+" : "−") + pct(Math.abs(h.pct)) + (h.price ? `<br><span class="muted" style="font-size:.8rem">at ${inr(h.price, 0)}</span>` : "");
  return `<span class="src ${h.direction === "up" ? "gain" : "loss"}"${tip(h.note[0].toUpperCase() + h.note.slice(1) + ".\n\n" + each + how)}>${txt}</span>`;
}
// Price ÷ conservative value, red when above the price-discipline limit.
function valueCell(mult, lim) {
  if (mult == null) return `<span class="muted"${tip("No positive value estimate (e.g. negative free cash flow while investing heavily), so the price limit can't be checked")}>n/a</span>`;
  const over = lim && mult > lim;
  return `<span class="src ${over ? "loss" : ""}"${tip(`Market value is ${mult.toFixed(2)}x the program's conservative value estimate (DCF, or justified P/B for lenders). `
    + (lim ? `Your price-discipline limit is ${lim}x${over ? ": above it" : ""}.` : "Price discipline is off."))}>${mult.toFixed(1)}×</span>`;
}
const SIG = {hold: "In portfolio", buy: "Buy", sell: "Sell", watch: "Watching"};
const sigChip = (s, why) => s ? `<span class="sig ${s}"${tip(why ? SIG[s] + ": " + why : "")}>${SIG[s]}</span>` : "";
function t250Filter(r) {
  const f = t250, q = f.q.trim().toUpperCase();
  if (q && !r.symbol.includes(q) && !r.name.toUpperCase().includes(q)) return false;
  if (f.data === "imported" && !r.imported) return false;
  if (f.data === "missing" && r.imported) return false;
  if (f.data === "short" && !(r.imported && r.years < 10)) return false;
  if (f.data === "provisional" && !r.provisional) return false;
  if (f.data === "noprices" && !(r.imported && !r.has_prices)) return false;
  if (f.data === "not_nifty" && r.in_nifty) return false;
  if (f.industry && r.industry !== f.industry) return false;
  if (f.category && r.category !== f.category) return false;
  if (f.size && r.size !== f.size) return false;
  for (const [k, key] of [["minB", "buffett"], ["minM", "munger"], ["minL", "lynch"]])
    if (f[k] !== "" && !((r[key]?.score ?? -1) >= +f[k])) return false;
  const n = (r.matches || []).length;
  switch (f.bucket) {
    case "quality": return has(r, "buffett") && has(r, "munger");
    case "growth": return has(r, "lynch");
    case "buffett": case "munger": return has(r, f.bucket);
    case "two_plus": return n >= 2;
    case "all_three": return n === 3;
    case "any_match": return n >= 1;
    case "none": return r.imported && n === 0;
    case "needs_input": return ["buffett", "munger", "lynch"].some(k => (r[k]?.unknown || []).length);
  }
  return true;
}
const SORTS = {
  symbol: r => r.symbol, industry: r => r.industry + r.symbol, years: r => r.years ?? -1,
  buffett: r => r.buffett?.score ?? -1, munger: r => r.munger?.score ?? -1, lynch: r => r.lynch?.score ?? -1,
  mcap: r => r.market_cap_cr ?? -1, pe: r => r.pe ?? 1e9,
  roce: r => r.returns?.applicable ? r.returns.roce ?? -9 : -10, roic: r => r.returns?.applicable ? r.returns.roic ?? -9 : -10,
  default: r => r.imported ? (r.match_count || 0) * 1000 + (r.buffett?.score || 0) + (r.munger?.score || 0) + (r.lynch?.score || 0) : -1};
function t250Sorted(rows) {
  const key = SORTS[t250.sort] || SORTS.default, d = t250.dir;
  return [...rows].sort((a, b) => { const x = key(a), y = key(b); return (x > y ? 1 : x < y ? -1 : a.symbol.localeCompare(b.symbol) * -d) * d; });
}
async function top250() {
  const d = await guard(() => api("/api/top250?universe=" + t250.universe)); if (!d) return;
  t250data = d;
  const opts = (vals, cur, all) => `<option value="">${all}</option>` + vals.map(v => `<option ${cur === v ? "selected" : ""}>${esc(v)}</option>`).join("");
  const inds = [...new Set(d.rows.map(r => r.industry))].sort();
  const cats = [...new Set(d.rows.map(r => r.category).filter(Boolean))].sort();
  const sel = (id, list, cur) => `<select id="${id}">${list.map(([v, l]) => `<option value="${v}" ${cur === v ? "selected" : ""}>${l}</option>`).join("")}</select>`;
  main.innerHTML = `<h2>Top 250</h2>
  <p class="lead">The Nifty LargeMidcap 250 is your screening universe. All 250 are always listed; scores appear once a company's
  Screener export is imported. Size bands use ${esc(d.size_basis)}.</p>
  <section class="box"><details id="datatools" ${t250.toolsOpen ? "open" : ""}><summary>${d.imported} of ${d.total} imported
    <span class="muted" style="font-weight:400">(${d.ten_year} with 10 years) · import &amp; update data</span></summary>
    <div class="progress"><i style="width:${d.imported / d.total * 100}%"></i></div>
    <details><summary><strong>Import many files at once</strong></summary>
    <div class="row" style="margin-top:8px"><label>Screener exports (.xlsx), select as many as you like<input type="file" id="bulk" multiple accept=".xlsx,.csv"></label>
    <label>Statements<select id="bbasis"><option value="consolidated">Consolidated</option><option value="standalone">Standalone</option></select></label></div>
    <p class="muted" style="font-size:.867rem">Each file is matched to its company by the name inside it, so file names don't matter.
    Banks, NBFCs and insurers are set automatically from the index list; you can change this on the company's page.</p>
    <div class="imp-log" id="implog"></div></details>
    <details style="margin-top:8px" ${d.mcap_universe_available ? "" : "open"}><summary><strong>Import the combined top-250 workbook</strong></summary>
    <div class="row" style="margin-top:8px"><label>NSE_Top250_Financials…xlsm (all companies in one file)<input type="file" id="wbfile" accept=".xlsm,.xlsx"></label>
    <label style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="wbkeep" checked> Keep companies already imported from single Screener exports (they have quarters and prices)</label></div>
    <p class="muted" style="font-size:.867rem">The workbook has no year-end share prices. After importing, fetch them from Yahoo so the retained-earnings test can run and market caps are current.</p>
    <div id="wbres"></div></details>
    <div class="row" style="margin-top:8px"><button class="btn ghost" id="finref" title="Normally automatic: weekly in May-June and November-December">Refresh financials from Screener.in now</button>
      <span class="muted" id="finstat" style="align-self:center">Automatic every week in results season (May–June, Nov–Dec). Takes about 15 minutes.</span></div>
    <div class="row" style="margin-top:8px"><button class="btn ghost" id="fillp">Fill year-end prices and market caps from Yahoo</button>
      <span class="muted" id="fillstat" style="align-self:center">${d.missing_prices ? d.missing_prices + " imported companies have no year-end prices yet" : ""}</span></div>
    <details style="margin-top:8px"><summary>Refresh current prices from a snapshot CSV (Ticker, Latest_Price)</summary>
    <p class="muted" style="font-size:.867rem;margin-top:6px">A quick, offline way to bring valuations (P/E, P/B, margin of safety) up to date between full Yahoo refreshes.
    It updates today's price and market cap only — it does not supply the year-by-year history the retained-earnings test needs; use
    "Fill year-end prices from Yahoo" above for that.</p>
    <div class="row" style="margin-top:6px"><input type="file" id="lpfile" accept=".csv"></div>
    <div id="lpres"></div></details>
    <details style="margin-top:8px"><summary>Update the index list (after NSE's March and September rebalancing)</summary>
    <div class="row" style="margin-top:8px"><label>New ind_niftylargemidcap250list.csv from niftyindices.com<input type="file" id="idx" accept=".csv"></label></div></details>
  </details></section>
  <section class="box">
    <div class="row" style="margin-bottom:10px"><span class="muted" style="align-self:center">Quick views:</span>
      ${PRESETS.map(([v, l]) => `<button class="btn ${t250.bucket === v ? "" : "ghost"}" data-preset="${v}">${l}</button>`).join("")}
      <label style="flex-direction:row;align-items:center;gap:.4rem;margin-left:.6rem">Sector<select id="find">${opts(inds, t250.industry, "All sectors")}</select></label>
      <button class="link" id="reset" style="align-self:center">Reset all filters</button></div>
    <details id="morefilters" ${t250.filtersOpen ? "open" : ""}><summary>More filters${activeFilters() ? ` <span class="chip">${activeFilters()} active</span>` : ""}</summary>
    <div class="filters">
      <label>Universe${sel("funi", [["nifty", "Nifty LargeMidcap 250"], ["mcap", "Top 250 by market cap"], ["both", "Both (union)"]], t250.universe)}</label>
      <label>Search<input id="fq" value="${esc(t250.q)}" placeholder="name or symbol" size="14"></label>
      <label>Matches${sel("fbucket", BUCKET_FILTERS, t250.bucket)}</label>
      <label>Data${sel("fdata", [["all", "All 250"], ["imported", "Imported"], ["missing", "Not imported yet"], ["short", "Under 10 years"], ["provisional", "Provisional (under 6 years)"], ["noprices", "Missing year-end prices"], ["not_nifty", "Not in the Nifty 250"]], t250.data)}</label>
      <label>Lynch category<select id="fcat">${opts(cats, t250.category, "All categories")}</select></label>
      <label>Size${sel("fsize", [["", "All sizes"], ["large", "Large cap"], ["mid", "Mid cap"], ["small", "Small cap"]], t250.size)}</label>
      <label>Buffett ≥<input id="fminB" type="number" min="0" max="100" step="5" value="${t250.minB}" style="width:5rem"></label>
      <label>Munger ≥<input id="fminM" type="number" min="0" max="100" step="5" value="${t250.minM}" style="width:5rem"></label>
      <label>Lynch ≥<input id="fminL" type="number" min="0" max="100" step="5" value="${t250.minL}" style="width:5rem"></label>
    </div>
    <div class="row" style="margin-bottom:6px">
      <button class="btn ghost" id="exp">Export shown as CSV</button>
      <button class="btn ghost" id="slall">Shortlist all shown</button>
      <button class="btn ghost" id="bulkman">Fill in promoter / pledge / institutional % for shown stocks</button></div></details>
    <div class="row" style="margin-top:8px"><span id="t250count" class="muted" style="align-self:center"></span></div>
    <div class="legend"><span><span class="sig hold">In portfolio</span> you hold it and it still matches</span>
      <span><span class="sig buy">Buy</span> shortlisted, not held, matches at today's price</span>
      <span><span class="sig sell">Sell</span> you hold it but it no longer matches, or its price is above your price-discipline limit</span></div>
  </section>
  <section class="box scroll" id="t250table"></section>
  <section class="box" id="manualbox" hidden></section>`;
  renderT250();
  $("#morefilters").addEventListener("toggle", e => { t250.filtersOpen = e.target.open; });
  $("#datatools").addEventListener("toggle", e => { t250.toolsOpen = e.target.open; });
  const bind = (id, key, ev = "change") => $("#" + id).addEventListener(ev, e => { t250[key] = e.target.value; renderT250(); });
  bind("fq", "q", "input"); bind("fbucket", "bucket"); bind("fdata", "data"); bind("find", "industry"); bind("fcat", "category");
  bind("fsize", "size"); bind("fminB", "minB", "input"); bind("fminM", "minM", "input"); bind("fminL", "minL", "input");
  $("#funi").onchange = e => { t250.universe = e.target.value; top250(); };
  $("#wbfile").onchange = e => guard(async () => { const f = e.target.files[0]; if (!f) return;
    $("#wbres").innerHTML = '<p class="muted">Reading the workbook (a few seconds)…</p>';
    const r = await api("/api/import/workbook?keep_exports=" + ($("#wbkeep").checked ? "1" : "0"), await f.arrayBuffer(), true);
    status(`Imported ${r.imported.length} companies from the workbook`, false, 8000); await top250();
    $("#wbres").innerHTML = `<p>Imported ${r.imported.length}; kept ${r.merged_with_exports.length} existing Screener exports (added reported capex).
      ${r.skipped.length ? "Skipped (too little history): " + r.skipped.map(x => esc(x.symbol)).join(", ") : ""}</p>`;
    $("#wbres").closest("details").open = true; });
  $("#fillp").onclick = () => guard(async () => { await api("/api/fill-prices", {}); pollPrices(); });
  $("#finref").onclick = () => guard(async () => {
    if (!confirm("Re-read every company's financials from Screener.in now? It runs in the background for about 15 minutes; "
      + "your manual entries and prices are kept.")) return;
    await api("/api/refresh-financials", {}); pollFin(); });
  pollFin(true);
  $("#lpfile").onchange = e => guard(async () => { const f = e.target.files[0]; if (!f) return;
    $("#lpres").innerHTML = '<p class="muted">Updating…</p>';
    const r = await api("/api/import/latest-prices", await f.text(), true);
    status(`Refreshed current price for ${r.updated.length} companies`, false, 6000);
    const msg = `<p>Updated ${r.updated.length} of ${r.in_file} prices in the file.
      ${r.skipped_no_shares.length ? r.skipped_no_shares.length + " skipped (no share count yet): " + r.skipped_no_shares.slice(0, 10).map(esc).join(", ") + (r.skipped_no_shares.length > 10 ? "…" : "") : ""}
      ${r.not_yet_imported ? r.not_yet_imported + " companies in the file aren't imported yet, so had nothing to update." : ""}</p>`;
    await top250(); $("#lpres").innerHTML = msg; $("#lpfile").closest("details").open = true; });
  $("#bulkman").onclick = () => renderManualBulk();
  main.querySelectorAll("[data-preset]").forEach(b => b.onclick = () => { t250.bucket = t250.bucket === b.dataset.preset ? "any" : b.dataset.preset; top250(); });
  $("#reset").onclick = () => { t250 = {...T0}; top250(); };
  $("#exp").onclick = () => { const syms = t250Shown().map(r => r.symbol).join(",");
    location.href = "/export.csv?symbols=" + encodeURIComponent(syms); };
  $("#slall").onclick = () => guard(async () => {
    const shown = t250Shown().filter(r => r.imported && !r.shortlisted);
    if (!shown.length) return status("Nothing new to shortlist in this view (only imported, not-yet-shortlisted stocks can be added)");
    if (!confirm(`Add ${shown.length} stocks to the shortlist? They will be watched by the daily news check.`)) return;
    const r = await api("/api/shortlist/add-many", {symbols: shown.map(x => x.symbol)});
    status(`Shortlisted ${r.added.length} stocks`); top250(); });
  $("#idx").onchange = e => guard(async () => { const f = e.target.files[0]; if (!f) return;
    const r = await api("/api/index-list", await f.text(), true); status(`Index list updated: ${r.companies} companies`); top250(); });
  $("#bulk").onchange = e => bulkImport([...e.target.files], $("#bbasis").value);
}
async function pollFin(quiet) {
  const j = await api("/api/fin-job").catch(() => null), el = $("#finstat"); if (!j || !el) return;
  if (j.running) { el.textContent = `Reading financials: ${j.done} of ${j.total}…`; setTimeout(pollFin, 4000); }
  else if (j.result && !quiet) { el.textContent = `Done: ${j.result.updated.length} with new results, ${Object.keys(j.result.failed).length} failed (see Notifications).`; refreshUnread(); }
}
async function pollPrices() {
  const j = await api("/api/price-job").catch(() => null); if (!j) return;
  const el = $("#fillstat");
  if (el) el.textContent = j.running ? `Fetching prices: ${j.done} of ${j.total}…` : j.finished ? `Done: ${j.total - j.errors.length} of ${j.total} filled${j.errors.length ? "; " + j.errors.length + " failed (see errors log)" : ""}` : "";
  if (j.running) setTimeout(pollPrices, 2500); else if (j.finished && location.hash.startsWith("#top250")) { const keep = el?.textContent; await top250(); if ($("#fillstat")) $("#fillstat").textContent = keep; }
}
function renderManualBulk() {
  const box = $("#manualbox");
  const rows = t250Shown().filter(r => r.imported).slice(0, 80);
  if (!rows.length) { status("No imported stocks in the current filter — narrow it first (e.g. Quality core, or Matches: Two or more)", true); return; }
  box.hidden = false;
  const lender = r => r.sector_type === "bank" || r.sector_type === "nbfc";
  box.innerHTML = `<h3 style="margin-top:0">Fill in ownership data (${rows.length}${rows.length === 80 ? "+, showing first 80" : ""} stocks from the current filter)</h3>
    <p class="muted" style="font-size:.867rem">Open each company's Screener page, scroll to <b>Shareholding Pattern</b> for the latest quarter's Promoter, FII and DII holding
    (add FII + DII together for one number), and its <b>Documents/Concalls or the promoter row itself</b> for pledge %. Banks and NBFCs also need Gross NPA %
    from their latest results. Leave a box blank to skip it — nothing already saved is cleared. Click Save once at the end.</p>
    <div class="scroll"><table><thead><tr><th class="l">Stock</th><th>Promoter %</th><th>Pledge %</th><th>FII+DII %</th>${rows.some(lender) ? "<th>Gross NPA %</th>" : ""}<th class="l"></th></tr></thead>
    <tbody>${rows.map(r => `<tr data-row="${esc(r.symbol)}"><td class="l">${esc(r.symbol)}<br><span class="muted">${esc(r.name)}</span></td>
      <td><input type="number" min="0" max="100" step="0.1" data-f="promoter_pct" value="${r.manual?.promoter_pct ?? ""}" style="width:5rem"></td>
      <td><input type="number" min="0" max="100" step="0.1" data-f="pledge_pct" value="${r.manual?.pledge_pct ?? ""}" style="width:5rem"></td>
      <td><input type="number" min="0" max="100" step="0.1" data-f="institutional_pct" value="${r.manual?.institutional_pct ?? ""}" style="width:5rem"></td>
      ${rows.some(lender) ? `<td>${lender(r) ? `<input type="number" min="0" max="100" step="0.01" data-f="gnpa_pct" value="${r.manual?.gnpa_pct ?? ""}" style="width:5rem">` : ""}</td>` : ""}
      <td class="l"><a href="https://www.screener.in/company/${esc(r.symbol)}/${r.sector_type === "bank" || r.sector_type === "nbfc" ? "" : "consolidated/"}#shareholding"
        target="_blank" rel="noopener">Screener ↗</a></td></tr>`).join("")}</tbody></table></div>
    <div class="row" style="margin-top:10px"><button class="btn" id="savemanual">Save all</button>
      <button class="btn ghost" id="closemanual">Close</button><span id="manualstat" class="muted" style="align-self:center"></span></div>`;
  $("#closemanual").onclick = () => { box.hidden = true; box.innerHTML = ""; };
  $("#savemanual").onclick = () => guard(async () => {
    const data = [...box.querySelectorAll("tr[data-row]")].map(tr => {
      const row = {symbol: tr.dataset.row};
      tr.querySelectorAll("input[data-f]").forEach(inp => { if (inp.value !== "") row[inp.dataset.f] = inp.value; });
      return row;
    }).filter(r => Object.keys(r).length > 1);
    if (!data.length) return status("Nothing entered yet");
    const r = await api("/api/manual-bulk", {rows: data});
    $("#manualstat").textContent = `Saved ${r.saved.length} of ${data.length}` + (r.errors.length ? `; ${r.errors.length} failed` : "");
    status(`Saved ownership data for ${r.saved.length} stocks`); box.hidden = true; box.innerHTML = ""; renderT250();
  });
}
function t250Shown() { return t250Sorted(t250data.rows.filter(t250Filter)); }
function renderT250() {
  const shown = t250Shown(), d = t250data;
  $("#t250count").textContent = `${shown.length} of ${d.rows.length} companies shown`;
  const sc = (r, k) => !r[k] ? '<span class="muted">–</span>' : `<span class="src ${r[k].matches ? "gain" : ""}" title="${esc((r[k].failed.length ? "Fails: " + r[k].failed.join(", ") + "\n" : "") + (r.src?.scores || ""))}">${r[k].score == null ? "–" : r[k].score.toFixed(0)}${r[k].matches ? " ✓" : ""}</span>`;
  const th = (key, label, cls = "") => `<th class="${cls}"><button class="link sort" data-sort="${key}" style="text-decoration:none;color:inherit;font-weight:600">${label}${t250.sort === key ? (t250.dir > 0 ? " ▲" : " ▼") : ""}</button></th>`;
  const bucketChip = r => ({all_three: '<span class="chip all">All three</span>', quality_core: '<span class="chip b">Quality core</span>',
    two_of_three: '<span class="chip m">Two of three</span>', lynch_only: '<span class="chip y">Growth</span>',
    buffett_only: '<span class="chip b">Buffett</span>', munger_only: '<span class="chip m">Munger</span>'})[r.bucket] || "";
  $("#t250table").innerHTML = shown.length ? `<table><thead><tr>${th("symbol", "Company", "l")}${th("industry", "Industry / type", "l")}${th("years", "Data", "l")}
    ${th("mcap", "Mkt cap (₹ cr)")}${th("pe", "P/E")}${th("roce", "ROCE")}${th("roic", "ROIC")}${th("buffett", "Buffett")}${th("munger", "Munger")}${th("lynch", "Lynch")}${th("default", "Bucket", "l")}</tr></thead>
    <tbody>${shown.map(r => `<tr class="${r.signal ? "sig-" + r.signal : ""}"><td class="l"><button class="link" data-an="${esc(r.symbol)}">${esc(r.symbol)}</button>${r.in_nifty ? "" : ' <span class="chip" title="Not in the Nifty LargeMidcap 250">mcap list</span>'}<br><span class="muted">${esc(r.name)}</span></td>
    <td class="l">${esc(r.industry)}<br><span class="muted">${esc(r.sector_type || r.suggested_type)}${r.category ? " · " + esc(r.category) : ""}${r.size ? " · " + esc(r.size) : ""}</span></td>
    <td class="l">${r.imported ? srcSpan(`${r.years} yrs`, r.src?.financials) + (r.provisional ? '<br><span class="st-warn">provisional</span>' : "") : '<span class="muted">not imported</span>'}</td>
    <td>${r.market_cap_cr ? srcSpan(Math.round(r.market_cap_cr).toLocaleString("en-IN"), r.src?.market_cap) : "–"}</td>
    <td>${r.pe ? srcSpan(r.pe.toFixed(1), r.src?.pe) : "–"}</td>
    <td>${retCell(r, "roce")}</td><td>${retCell(r, "roic")}</td>
    <td>${sc(r, "buffett")}</td><td>${sc(r, "munger")}</td><td>${sc(r, "lynch")}</td>
    <td class="l">${bucketChip(r)}${["buffett", "munger", "lynch"].some(k => (r[k]?.unknown || []).length) ? '<span class="chip warnchip" title="A must-pass test needs your input, e.g. gross NPA">needs input</span>' : ""}${sigChip(r.signal, r.signal_note)}</td></tr>`).join("")}</tbody></table>`
    : '<p class="muted">No companies match these filters.</p>';
  $("#t250table").querySelectorAll("[data-sort]").forEach(b => b.onclick = () => {
    const k = b.dataset.sort;
    if (t250.sort === k) t250.dir *= -1; else { t250.sort = k; t250.dir = ["symbol", "industry", "pe"].includes(k) ? 1 : -1; }
    renderT250(); });
  bindAnalyseLinks();
}
async function bulkImport(files, basis) {
  const log = $("#implog"); let ok = 0;
  log.innerHTML = "";
  for (const [i, f] of files.entries()) {
    const line = document.createElement("div"); line.textContent = `${i + 1}/${files.length} ${f.name}…`; log.prepend(line);
    await importOne(f, basis, line, "").then(r => { if (r) ok++; });
  }
  status(`Imported ${ok} of ${files.length} files`, ok < files.length, 8000);
  const keep = log.innerHTML; await top250(); $("#implog").innerHTML = keep; bindRetry(basis);
}
const pending = new Map();
async function importOne(f, basis, line, symbol) {
  try {
    const q = new URLSearchParams({filename: f.name, basis, symbol});
    const r = await api("/api/import/auto?" + q, await f.arrayBuffer(), true);
    if (r.ok) {
      line.innerHTML = `<span class="st-ok">✓</span> ${esc(f.name)} → <strong>${esc(r.symbol)}</strong> ${r.years} yrs to ${esc(r.last_year.slice(0, 4))}, ${esc(r.sector_type)}` +
        (r.in_top250 ? "" : ' <span class="st-warn">(not in the top 250 list)</span>') +
        (r.problems.length ? `<br><span class="st-warn">${r.problems.map(p => esc(p.check + ": " + p.detail)).join("<br>")}</span>` : "");
      return true;
    }
    const id = "p" + Math.random().toString(36).slice(2); pending.set(id, f);
    line.innerHTML = `<span class="st-warn">?</span> ${esc(f.name)} (${esc(r.company || "no name")}): ${esc(r.reason)}
      <input list="stocklist" size="12" placeholder="${esc(r.best_guess || "symbol")}" data-pid="${id}"> <button class="link" data-retry="${id}">Import as this</button>`;
  } catch (e) { line.innerHTML = `<span class="st-fail">✗</span> ${esc(f.name)}: ${esc(e.message)}`; }
  return false;
}
function bindRetry(basis) {
  document.querySelectorAll("[data-retry]").forEach(b => b.onclick = async () => {
    const id = b.dataset.retry, sym = symOf($(`[data-pid="${id}"]`).value), line = b.parentElement;
    if (!sym) return status("Type the symbol first", true);
    if (await importOne(pending.get(id), basis, line, sym)) pending.delete(id);
  });
}

// ---- scores
async function scores() {
  const d = await guard(() => api("/api/scores")); if (!d) return;
  const cell = x => x.score == null ? "–" : `<span class="${x.matches ? "gain" : ""}">${x.score.toFixed(0)}%</span>` +
    (x.coverage < 1 ? `<br><span class="muted" style="font-size:.8rem">${pct(x.coverage, 0)} of data</span>` : "");
  const mine = d.rows.filter(r => r.signal && r.signal !== null);
  const order = {sell: 0, buy: 1, hold: 2, watch: 3};
  mine.sort((a, b) => order[a.signal] - order[b.signal] || a.symbol.localeCompare(b.symbol));
  const count = s => mine.filter(r => r.signal === s).length;
  main.innerHTML = `<h2>Scores &amp; shortlist</h2>
  <section class="box scroll"><h3 style="margin-top:0">Your shortlist and portfolio</h3>
  <div class="legend"><span><span class="sig sell">Sell</span> ${count("sell")} · you hold it but it matches no philosophy, or it costs more than your limit (Settings → price discipline)</span>
    <span><span class="sig buy">Buy</span> ${count("buy")} · shortlisted, not held, matches, and priced within your limit</span>
    <span><span class="sig hold">In portfolio</span> ${count("hold")} · held and still matches</span>
    <span><span class="sig watch">Watching</span> ${count("watch")} · shortlisted, but not matching or too expensive right now</span></div>
  ${mine.length ? `<table><thead><tr><th class="l">Stock</th><th class="l">Signal</th><th>Price</th><th title="How far the price can move before the signal changes, all else equal">Price trigger</th><th title="Market value divided by the program's conservative value estimate">Price ÷ value</th><th>P/E</th><th>ROCE</th><th>Buffett</th><th>Munger</th><th>Lynch</th><th class="l">Why</th></tr></thead>
  <tbody>${mine.map(r => `<tr class="sig-${r.signal}"><td class="l"><button class="link" data-an="${esc(r.symbol)}">${esc(r.symbol)}</button>
      ${r.held ? `<br><span class="muted">${r.quantity} shares</span>` : ""}</td>
    <td class="l">${sigChip(r.signal, r.signal_note)}</td>
    <td>${r.price == null ? "–" : srcSpan(inr(r.price), r.src?.price) + `<br><span class="muted" style="font-size:.8rem">${esc(r.price_date || "")}</span>`}</td>
    <td>${trigger(r.headroom)}</td>
    <td>${valueCell(r.value_multiple, d.price_discipline)}</td>
    <td>${r.pe == null ? "–" : srcSpan(r.pe.toFixed(1), r.src?.pe)}</td><td>${retCell(r, "roce")}</td>
    ${r.error ? `<td class="l muted" colspan="4">${esc(r.error)}</td>` : `<td>${cell(r.buffett)}</td><td>${cell(r.munger)}</td><td>${cell(r.lynch)}</td>
    <td class="l" style="white-space:normal">${esc(r.signal_note[0].toUpperCase() + r.signal_note.slice(1))}${r.matches.length ? "" :
      ". Fails: " + esc([...new Set(["buffett", "munger", "lynch"].flatMap(k => r[k].failed))].slice(0, 3).join(", ") || "below the pass mark")}</td>`}</tr>`).join("")}</tbody></table>`
    : '<p class="muted">Nothing shortlisted or held yet. Shortlist stocks below or on the Top 250 page, or record purchases on My portfolio.</p>'}
  <p class="muted" style="font-size:.867rem">A signal is a prompt to look closer, not an instruction: it uses today's price and the latest imported financials.
  Every change of signal is recorded in the Recommendation log.</p></section>
  <h3>All imported companies</h3>
  <p class="lead">Ranked by how many philosophies each matches (pass mark ${d.pass_score}%), then total score.
  Where some data was missing, the share of the test that ran is shown underneath.</p>
  <div class="row" style="margin-bottom:12px"><button class="btn" id="all3">Shortlist every stock matching all three</button>
    <label style="flex-direction:row;align-items:center;gap:.4rem;margin-left:.6rem">Sector<select id="ssector"><option value="">All sectors</option>
      ${[...new Set(d.rows.filter(r => r.industry).map(r => r.industry))].sort().map(s => `<option ${scoreSector === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></label>
    <span class="muted" id="scount" style="align-self:center"></span></div>
  <section class="box scroll" id="stable"></section>`;
  const render = () => {
    const rows = d.rows.filter(r => !(r.error && !r.name && r.signal)).filter(r => !scoreSector || r.industry === scoreSector);
    $("#scount").textContent = `${rows.length} companies`;
    $("#stable").innerHTML = rows.length ? `<table><thead><tr><th class="l">Stock</th><th class="l">Sector / type</th><th>ROCE</th><th>ROIC</th><th>Buffett</th><th>Munger</th><th>Lynch</th><th class="l">Matches</th><th class="l">Shortlist</th></tr></thead>
    <tbody>${rows.map(r => r.error ? `<tr><td class="l">${esc(r.symbol)}</td><td class="l loss" colspan="8">${esc(r.error)}</td></tr>` :
      `<tr class="${r.signal ? "sig-" + r.signal : ""}"><td class="l"><button class="link" data-an="${esc(r.symbol)}">${esc(r.symbol)}</button><br><span class="muted">${esc(r.name || "")}</span></td>
      <td class="l">${esc(r.industry || "")}<br><span class="muted">${esc(r.category)} · ${esc(r.size || "")} · ${esc(r.sector_type)}</span></td>
      <td>${retCell(r, "roce")}</td><td>${retCell(r, "roic")}</td>
      <td${tip(r.src?.scores)}>${cell(r.buffett)}</td><td${tip(r.src?.scores)}>${cell(r.munger)}</td><td${tip(r.src?.scores)}>${cell(r.lynch)}</td>
      <td class="l">${r.all_three ? '<span class="chip all">All three</span>' : [["buffett", "b", "Buffett"], ["munger", "m", "Munger"], ["lynch", "y", "Lynch"]]
        .filter(([k]) => r[k].matches).map(([, c, n]) => `<span class="chip ${c}">${n}</span>`).join("") || '<span class="muted">none</span>'}</td>
      <td class="l">${sigChip(r.signal, r.signal_note)}</td></tr>`).join("")}</tbody></table>` :
      '<p class="muted">No imported companies in this sector.</p>';
    bindAnalyseLinks();
  };
  render();
  $("#ssector").onchange = e => { scoreSector = e.target.value; render(); };
  $("#all3").onclick = () => guard(async () => { const r = await api("/api/shortlist/all-three", {});
    status(r.added.length ? "Added " + r.added.join(", ") : "No new stocks match all three"); scores(); });
  bindAnalyseLinks();
}
let scoreSector = "";

// ---- alerts
async function alerts() {
  const d = await guard(() => api("/api/events")); if (!d) return;
  main.innerHTML = `<h2>News alerts</h2>
  <p class="lead">Once a day the program checks news for your shortlist and holdings. You get an alert when a stock falls abnormally
  (beyond the Nifty and its normal swings) in the session its news came out, and again when the price has levelled out.</p>
  <div class="row" style="margin-bottom:12px"><button class="btn" id="run">Run the daily check now</button>
  <span class="muted" id="jobinfo">${jobText(d.job)}</span></div>
  <section class="box scroll"><h3 style="margin-top:0">Events</h3>${d.events.length ? `<table><tbody>${d.events.map(evRow).join("")}</tbody></table>` : '<p class="muted">No events tracked yet.</p>'}</section>
  <section class="box"><h3 style="margin-top:0">Recent alerts</h3>${d.alerts_log ? `<pre>${esc(d.alerts_log)}</pre>` : '<p class="muted">No alerts yet.</p>'}</section>
  <section class="box"><h3 style="margin-top:0">Latest daily report</h3>${d.latest_report ? `<pre>${esc(d.latest_report)}</pre>` : '<p class="muted">No report yet.</p>'}</section>`;
  $("#run").onclick = () => guard(async () => { await api("/api/run-daily", {}); status("Daily check started"); pollJob(); });
}
function jobText(j) {
  if (!j) return "";
  if (j.running) return "Running since " + j.started.slice(11, 16) + "…";
  if (j.error) return "Last run failed: " + j.error;
  if (j.result) return `Last run ${j.finished.slice(11, 16)}: ${j.result.tracked} stocks, ${j.result.alerts} alerts, ${Object.keys(j.result.errors).length} errors`;
  return "";
}
async function pollJob() {
  const d = await api("/api/events").catch(() => null); if (!d) return;
  $("#jobline").textContent = jobText(d.job);
  if (d.job.running) setTimeout(pollJob, 3000); else if (location.hash.startsWith("#alerts")) alerts();
}

// ---- recommendation log
const REASONS = {shortlisted: "Shortlisted", removed_from_shortlist: "Removed from shortlist", alert_dip: "Alert: news-linked drop",
  alert_levelled: "Alert: levelled out", bought: "Bought", sold: "Sold", trades_imported: "Zerodha trades imported",
  signal_buy: "Signal: Buy", signal_sell: "Signal: Sell"};
async function ledger(sym) {
  const d = await guard(() => api("/api/recommendations" + (sym ? "?symbol=" + encodeURIComponent(sym) : ""))); if (!d) return;
  const sc = x => x == null ? "–" : x.toFixed(0) + "%";
  main.innerHTML = `<h2>Recommendation log${sym ? " · " + esc(sym) : ""}</h2>
  <p class="lead">A permanent record, written automatically whenever a stock is shortlisted or removed, an alert goes out, or you buy or sell.
  Each entry freezes the price, scores and full analysis at that moment, so you can look back at why something was flagged.
  Entries are never changed afterwards.</p>
  ${sym ? '<div class="row" style="margin-bottom:12px"><button class="btn ghost" id="allrec">Show every stock</button></div>' : ""}
  <section class="box scroll">${d.rows.length ? `<table><thead><tr><th class="l">When</th><th class="l">Stock</th><th class="l">What happened</th>
    <th>Price</th><th>Buffett</th><th>Munger</th><th>Lynch</th><th class="l">Matches</th><th class="l"></th></tr></thead><tbody>
    ${d.rows.map(r => `<tr><td class="l">${esc(r.logged_at.slice(0, 16).replace("T", " "))}</td>
      <td class="l"><button class="link" data-sym="${esc(r.symbol)}">${esc(r.symbol)}</button>${r.held ? ' <span class="chip">held</span>' : ""}</td>
      <td class="l">${esc(REASONS[r.reason] || r.reason)}</td>
      <td>${r.price == null ? "–" : inr(r.price) + `<br><span class="muted" style="font-size:.8rem">${esc(r.price_date || "")}</span>`}</td>
      <td>${sc(r.buffett)}</td><td>${sc(r.munger)}</td><td>${sc(r.lynch)}</td>
      <td class="l">${esc(r.matches.join(", ") || "none")}</td>
      <td class="l"><button class="link" data-id="${r.id}">details</button></td></tr>
      <tr hidden id="rec${r.id}"><td class="l" colspan="9"></td></tr>`).join("")}</tbody></table>` :
    '<p class="muted">Nothing recorded yet. Entries appear when you shortlist a stock, record a buy or sell, or an alert goes out.</p>'}</section>`;
  if ($("#allrec")) $("#allrec").onclick = () => go("ledger");
  main.querySelectorAll("[data-sym]").forEach(b => b.onclick = () => go("ledger", b.dataset.sym));
  main.querySelectorAll("[data-id]").forEach(b => b.onclick = () => guard(async () => {
    const row = $("#rec" + b.dataset.id);
    if (!row.hidden) { row.hidden = true; return; }
    const r = await api("/api/recommendations?id=" + b.dataset.id), a = r.analysis || {};
    const crit = k => (a[k]?.criteria || []).map(c => `<li>${esc(c.label)}: <b>${esc(c.value)}</b> ` +
      `<span class="muted">(${c.score == null ? "not scored" : Math.round(c.score * 100) + "% of " + c.weight} · ${esc(c.rule)})</span></li>`).join("");
    row.firstElementChild.innerHTML = `${r.note ? `<pre>${esc(r.note)}</pre>` : ""}
      ${a.symbol ? `<p>${esc(a.name || a.symbol)} · ${esc(a.lynch_category)} · ${esc(a.size)} cap · P/E ${a.pe == null ? "–" : a.pe.toFixed(1)} ·
        P/B ${a.pb == null ? "–" : a.pb.toFixed(1)} · market cap ${cr(a.market_cap_cr)}</p>
      ${["buffett", "munger", "lynch"].map(k => `<details><summary>${k[0].toUpperCase() + k.slice(1)}: ${sc(a[k]?.score)}
        ${a[k]?.matches ? "(matches)" : ""}${(a[k]?.failed_essentials || []).length ? " · fails " + esc(a[k].failed_essentials.join(", ")) : ""}</summary>
        <ul>${crit(k)}</ul></details>`).join("")}
      ${(a.warnings || []).length ? `<p class="muted">Notes: ${esc(a.warnings.join(" · "))}</p>` : ""}` : ""}
      ${(r.headlines || []).length ? `<p><b>Headlines at the time</b></p><ul class="news">${r.headlines.map(h =>
        `<li><a href="${esc(h.url)}" target="_blank" rel="noopener">${esc(h.title)}</a> <span class="muted">${esc(h.source || "")}</span></li>`).join("")}</ul>` : ""}`;
    row.hidden = false;
  }));
}

// ---- settings
async function settings() {
  const [d, p] = await Promise.all([guard(() => api("/api/settings")), guard(() => api("/api/presets"))]); if (!d || !p) return;
  const s = d.scoring, f = (k, l, h, step = "0.001") => `<label>${l}<input type="number" step="${step}" name="${k}" value="${s[k]}"><span style="font-size:.8rem">${h}</span></label>`;
  const when = t => esc(t.slice(0, 16).replace("T", " "));
  const diff = st => Object.entries(st).filter(([k, v]) => v !== p.defaults[k]).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ") || "defaults";
  main.innerHTML = `<h2>Settings</h2>
  <section class="box"><h3 style="margin-top:0">Scoring</h3>
    <div class="row" style="margin-bottom:14px">
      <button class="btn ghost" id="undo" ${p.can_undo ? "" : "disabled"} title="Go back to the previous saved settings">↶ Undo</button>
      <button class="btn ghost" id="redo" ${p.can_redo ? "" : "disabled"} title="Re-apply the change you undid">↷ Redo</button>
      <button class="btn ghost" id="rst" title="Back to the original default settings (can be undone)">Reset to defaults</button>
      <span class="muted" style="align-self:center">Version ${p.pos + 1} of ${Math.max(p.count, 1)}</span></div>
    <form class="row" id="sc">
    <label>10-year G-Sec yield<input type="number" step="0.00001" name="risk_free" value="${s.risk_free}">
      <span style="font-size:.8rem">updated automatically every day; not part of presets or undo</span></label>
    ${f("equity_premium", "Equity premium", "added to G-Sec for the discount rate")}
    ${f("terminal_growth", "Long-run growth", "after the 10-year period")}
    ${f("pass_score", "Pass mark (%)", "a philosophy matches at or above this", "1")}
    ${f("min_coverage", "Minimum data coverage", "0.7 = 70% of the test must run", "0.05")}
    ${f("large_cap_min_cr", "Large-cap floor (₹ cr)", "from AMFI's latest list", "1")}
    ${f("mid_cap_min_cr", "Mid-cap floor (₹ cr)", "from AMFI's latest list", "1")}
    <label title="Scores and matches are unchanged; this only affects the Buy / Sell colours and price triggers">Price discipline for Buy/Sell
      <select name="price_discipline">${[[0, "Off"], [3, "Above 3x value (lenient)"], [2, "Above 2x value (recommended)"], [1.5, "Above 1.5x value (strict)"]]
        .map(([v, l]) => `<option value="${v}" ${+s.price_discipline === v ? "selected" : ""}>${l}</option>`).join("")}
        ${[0, 3, 2, 1.5].includes(+s.price_discipline) ? "" : `<option value="${s.price_discipline}" selected>Above ${s.price_discipline}x value</option>`}</select>
      <span style="font-size:.8rem">a held stock priced above this multiple of its estimated value shows Sell; a shortlisted one can't show Buy</span></label>
    <button class="btn">Save scoring settings</button></form></section>
  <section class="box"><h3 style="margin-top:0">Presets</h3>
    <p class="muted" style="font-size:.867rem;margin-top:0">Save the current scoring settings under a name, then switch between them any time. Loading a preset can be undone.</p>
    <div class="row" style="margin-bottom:12px"><label>Name<input id="pname" placeholder="e.g. Strict value" maxlength="60"></label>
      <button class="btn" id="psave">Save current settings as a preset</button></div>
    ${p.presets.length ? `<table><thead><tr><th class="l">Preset</th><th class="l">Settings (differences from defaults)</th><th class="l">Saved</th><th class="l"></th></tr></thead><tbody>
      ${p.presets.map(x => `<tr><td class="l"><strong>${esc(x.name)}</strong></td><td class="l" style="white-space:normal">${esc(diff(x.settings))}</td>
        <td class="l">${when(x.saved_at)}</td><td class="l"><button class="btn ghost" data-load="${esc(x.name)}">Load</button>
        <button class="link" data-del="${esc(x.name)}">delete</button></td></tr>`).join("")}</tbody></table>`
      : '<p class="muted">No presets saved yet.</p>'}
    <details style="margin-top:14px"><summary>Every saved version (${p.count})</summary>
      <div class="hist"><table><tbody>${p.history.map((h, i) => { const idx = p.count - 1 - i;
        return `<tr><td class="l">${when(h.at)}${idx === p.pos ? ' <span class="chip all">current</span>' : ""}</td><td class="l">${esc(h.label)}</td>
        <td class="l" style="white-space:normal">${esc(diff(h.settings))}</td>
        <td class="l">${idx === p.pos ? "" : `<button class="link" data-ver="${idx}">restore</button>`}</td></tr>`; }).join("")}</tbody></table></div></details>
  </section>
  <section class="box"><h3 style="margin-top:0">Alert sensitivity</h3>
    <p class="muted" style="font-size:.867rem;margin-top:0">When a news-linked drop counts as abnormal, and when a price counts as "levelled out".
    Lower drop / sigma = more (and noisier) alerts; the PDF guide explains each. Defaults shown under each box.</p>
    <form class="row" id="al">${[
      ["event.min_abnormal_drop", "Minimum abnormal drop", "fraction: 0.03 = 3%", "0.005"],
      ["event.sigma_multiple", "Drop size in normal swings (σ)", "2 = twice the stock's usual daily noise", "0.1"],
      ["event.window_sessions", "Sessions counted for the drop", "news day + following sessions", "1"],
      ["levelling.sessions", "Levelling look-back (sessions)", "recent window checked for calm", "1"],
      ["levelling.min_sessions_after_event", "Earliest levelling check", "sessions after the drop window", "1"],
      ["levelling.atr_ratio_max", "Calm: recent range ≤ x pre-news", "1.25 = at most 25% choppier", "0.05"],
      ["levelling.drift_sigma_max", "Calm: drift ≤ x σ", "abnormal drift allowed over the window", "0.1"],
      ["levelling.no_new_low_sessions", "No new low for (sessions)", "the bottom must be this old", "1"],
      ["event.expire_after_sessions", "Stop tracking a drop after", "trading sessions (60 ≈ 3 months)", "1"]]
      .map(([k, l, h, step]) => `<label>${l}<input type="number" step="${step}" name="${k}" value="${d.alerts[k]}">
        <span style="font-size:.8rem">${h} · default ${d.alert_defaults[k]}</span></label>`).join("")}
      <button class="btn">Save alert sensitivity</button><button type="button" class="btn ghost" id="aldef">Back to defaults</button></form></section>
  <section class="box"><h3 style="margin-top:0">Alerts</h3><form class="row" id="nt">
    <label style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" name="windows_toast" ${d.windows_toast ? "checked" : ""}> Windows pop-up notifications</label>
    <label>Telegram bot token<input name="telegram_bot_token" placeholder="${d.telegram_configured ? "saved (enter to replace)" : "optional"}" size="30"></label>
    <label>Telegram chat ID<input name="telegram_chat_id" placeholder="optional" size="14"></label>
    <label>ntfy topic (phone app)<input name="ntfy_topic" value="${esc(d.ntfy_topic || "")}" placeholder="e.g. screener-k7q2m9x4" size="22" autocomplete="off">
      <span style="font-size:.8rem">leave blank to turn off</span></label>
    <label>ntfy server<input name="ntfy_server" value="${esc(d.ntfy_server || "https://ntfy.sh")}" size="18"></label>
    <label>Discord webhook URL<input name="discord_webhook" placeholder="${d.discord_configured ? "saved (paste to replace)" : "https://discord.com/api/webhooks/..."}" size="30" autocomplete="off">
</label>
    ${d.discord_configured ? '<label style="flex-direction:row;align-items:center;gap:.3rem"><input type="checkbox" name="discord_remove"> Remove the saved Discord webhook</label>' : ""}
    <button class="btn">Save alert settings</button><button type="button" class="btn ghost" id="test">Send a test alert</button></form></section>`;
  $("#sc").onsubmit = e => { e.preventDefault(); guard(async () => {
    await api("/api/settings", {scoring: Object.fromEntries(new FormData(e.target))}); status("Scoring settings saved"); settings(); }); };
  $("#al").onsubmit = e => { e.preventDefault(); guard(async () => {
    await api("/api/settings", {alerts: Object.fromEntries(new FormData(e.target))}); status("Alert sensitivity saved"); settings(); }); };
  $("#aldef").onclick = () => guard(async () => { await api("/api/settings", {alerts: d.alert_defaults}); status("Alert sensitivity back to defaults"); settings(); });
  const act = (body, msg) => guard(async () => { await api("/api/presets", body); status(msg); settings(); });
  $("#undo").onclick = () => act({action: "undo"}, "Undone");
  $("#redo").onclick = () => act({action: "redo"}, "Redone");
  $("#rst").onclick = () => { if (confirm("Reset scoring settings to the defaults? You can undo this.")) act({action: "reset"}, "Reset to defaults"); };
  $("#psave").onclick = () => { const name = $("#pname").value.trim(); if (!name) return status("Give the preset a name first", true);
    if (p.presets.some(x => x.name === name) && !confirm(`Replace the existing preset "${name}"?`)) return;
    act({action: "save", name}, `Saved preset "${name}"`); };
  main.querySelectorAll("[data-load]").forEach(b => b.onclick = () => act({action: "load", name: b.dataset.load}, `Loaded "${b.dataset.load}"`));
  main.querySelectorAll("[data-del]").forEach(b => b.onclick = () => { if (confirm(`Delete preset "${b.dataset.del}"?`)) act({action: "delete", name: b.dataset.del}, "Preset deleted"); });
  main.querySelectorAll("[data-ver]").forEach(b => b.onclick = () => act({action: "version", index: +b.dataset.ver}, "Version restored"));
  $("#nt").onsubmit = e => { e.preventDefault(); guard(async () => { const b = Object.fromEntries(new FormData(e.target));
    b.windows_toast = e.target.windows_toast.checked; await api("/api/settings", b); status("Alert settings saved"); settings(); }); };
  $("#test").onclick = () => guard(async () => { const r = await api("/api/notify-test", {});
    status(r.problems.length ? r.problems.join("; ") : "Test alert sent", r.problems.length > 0); });
}

// ---- help manual (the (?) button, top right)
function help() {
  const S = [
    ["start", "Getting started"], ["daily", "What happens automatically"], ["portfolio", "My portfolio"], ["analyse", "Analyse a stock"],
    ["top250", "Top 250"], ["scores", "Scores & shortlist (Buy / Sell colours)"], ["alerts", "News alerts"], ["ledger", "Recommendation log"],
    ["notes", "Notifications"], ["settings", "Settings, presets, undo / redo"], ["sources", "Where every number comes from"],
    ["metrics", "The measures explained"], ["appearance", "Themes, text size, sidebar"], ["trouble", "Troubleshooting"], ["cli", "Commands (optional)"]];
  main.innerHTML = `<div class="manual"><h2>How to use Value Screener</h2>
  <p class="lead">A free, private stock screener for Indian shares built on Warren Buffett's, Charlie Munger's and Peter Lynch's ideas.
  Everything runs on this PC. It suggests what to look at; it never buys or sells anything. A separate PDF, <b>Value_Screener_Guide.pdf</b>
  in the project folder, explains the finance and the maths behind every number.</p>
  <section class="box"><strong>Contents</strong><ol class="toc">${S.map(([id, t]) => `<li><a href="#help" data-jump="${id}">${esc(t)}</a></li>`).join("")}</ol></section>

  <section class="box">
  <h3 id="h-start">1. Getting started</h3>
  <ol><li>Double-click <b>Start Screener.bat</b>. The dashboard opens at <kbd>http://127.0.0.1:8765</kbd>; keep that window open while you use it.</li>
  <li>Record what you own on <b>My portfolio</b> (or import a Zerodha tradebook). Held stocks are watched automatically.</li>
  <li>Look through <b>Top 250</b> or <b>Scores &amp; shortlist</b> and shortlist the companies you want watched.</li>
  <li>The <b>daily check</b> (set up once with <b>Set up daily check.bat</b>) runs every weekday at 18:30 and does the rest.</li></ol>

  <h3 id="h-daily">2. What happens automatically</h3>
  <ul><li><b>Every weekday (18:30):</b> today's official NSE closing prices for all companies (Yahoo as backup); the 10-year G-Sec yield;
  news for your shortlist and holdings; drop and levelling-out checks; Buy/Sell signal changes; a daily report.</li>
  <li><b>Weekly:</b> promoter %, pledge %, FII + DII % (Screener.in / NSE filings), lenders' Gross NPA %, and any missing year-end prices.</li>
  <li><b>Weekly in results season</b> (May–June and November–December): every company's financial statements are re-read from Screener.in, so new
  annual and quarterly results flow into the scores. You can also start this from Top 250 → import &amp; update data.</li>
  <li>Anything you type in yourself is never overwritten by these updates.</li></ul>

  <h3 id="h-portfolio">3. My portfolio</h3>
  <ul><li>Add BUY / SELL with quantity and price, DIVIDEND with the total received, SPLIT or BONUS as a ratio (e.g. 5:1).</li>
  <li>Averages use first-in-first-out, like the tax rules. Values use the last completed daily close (settles after 4 pm).</li>
  <li>Every buy and sell is also recorded in the Recommendation log with the full analysis at that moment.</li></ul>

  <h3 id="h-analyse">4. Analyse a stock</h3>
  <ul><li>Type any NSE name or symbol. You see price history, the three philosophy scores, every test with its value, data checks and recent news.</li>
  <li><b>Hover</b> any figure with a dotted underline to see where it came from. Hover a test row for its rule and data source.</li>
  <li>"Must pass" tests are non-negotiable: a stock can score well and still not match if one fails.</li>
  <li>You can import a Screener.in export for a company, change its business type (general / bank / NBFC / insurance), or type ownership and NPA figures.</li></ul>

  <h3 id="h-top250">5. Top 250</h3>
  <ul><li>All 250 companies of the Nifty LargeMidcap 250. <b>Quick views</b> and the <b>Sector</b> filter are always visible;
  open <b>More filters</b> for search, match type, data completeness, Lynch category, size and minimum scores. The toggle shows how many hidden filters are active.</li>
  <li>Click a column heading to sort (again to reverse). ROCE and ROIC show "n/a" for banks, NBFCs and insurers, where they don't apply.</li>
  <li><b>Import &amp; update data</b> (click the "imported" line) holds the file imports and the manual refresh buttons.</li></ul>

  <h3 id="h-scores">6. Scores &amp; shortlist</h3>
  <ul><li><span class="sig sell">Sell</span> you hold it and either it matches none of the three philosophies, or its price is above your
  <b>price-discipline</b> limit (default: more than 2x the program's conservative value estimate).</li>
  <li><span class="sig buy">Buy</span> shortlisted, not held, matches at least one philosophy, and is priced within that limit.</li>
  <li><span class="sig hold">In portfolio</span> held, still matches, within the limit. <span class="sig watch">Watching</span> shortlisted but not matching, or too expensive.</li>
  <li>Why the limit: Munger's tests are mostly about business quality, so a great business keeps "matching" even when very expensive. The limit
  stops that becoming a green Buy. It never changes the scores. Change it (Off / 3x / 2x / 1.5x) in Settings. Hover a signal to see the reason;
  the <b>Price ÷ value</b> column shows how expensive each stock is and <b>Price trigger</b> shows the move that would flip the signal.</li>
  <li>These are prompts to look closer, not instructions. Changes to Buy or Sell are recorded in the Recommendation log.</li></ul>

  <h3 id="h-alerts">7. News alerts</h3>
  <ul><li>For shortlisted and held stocks, the program links news to price moves. An alert means the stock fell <b>abnormally</b> (after allowing
  for the Nifty's move and the stock's normal swings) in the session its news came out.</li>
  <li>A second alert says when the price has <b>levelled out</b>: calm trading, no further abnormal drift, and no new low for a few sessions.
  It does not predict a rise and is not a sell signal. The PDF guide gives the exact limits.</li>
  <li>"Run the daily check now" runs it immediately.</li></ul>

  <h3 id="h-ledger">8. Recommendation log</h3>
  <ul><li>A permanent record of every shortlisting, removal, alert, Buy/Sell signal change, buy and sell, each with the price, scores, every test
  and the headlines at that moment. Entries are never changed. Click <b>details</b> to see the frozen analysis.</li></ul>

  <h3 id="h-notes">9. Notifications (the bell)</h3>
  <ul><li><span class="note stock" style="display:inline;padding:0 .4rem;border-left-width:1px">Light blue</span> stock and news alerts, with the full text sent to your pop-ups.</li>
  <li><span class="note program" style="display:inline;padding:0 .4rem;border-left-width:1px">Light yellow</span> about the program: a data source failed,
  a refresh finished or partly failed, prices went stale, the daily check crashed or hasn't run. These never pop up; check the bell's red count.</li></ul>

  <h3 id="h-settings">10. Settings, presets, undo / redo</h3>
  <ul><li><b>Pass mark</b>: the score a philosophy must reach to "match" (default 65%). Lower = more matches, less strict.</li>
  <li><b>Equity premium</b> and <b>long-run growth</b> drive the valuation (discount rate = G-Sec + premium).</li>
  <li><b>Minimum data coverage</b>: how much of a test must have data before a match is trusted.</li>
  <li>Every save is a version: <b>Undo</b>, <b>Redo</b>, <b>Reset to defaults</b>, or restore any version from the list.
  Save named <b>presets</b> (e.g. "Strict value", "Higher risk") and switch between them. The G-Sec yield updates itself and is excluded.</li></ul>

  <h3 id="h-sources">11. Where every number comes from</h3>
  <ul><li><b>Prices:</b> NSE's official daily closing-price file (Yahoo Finance as backup and for price history).</li>
  <li><b>Financial statements:</b> the Top 250 workbook, Screener.in public pages, or your Screener exports.</li>
  <li><b>Promoter / FII / DII %:</b> Screener.in shareholding. <b>Pledge %:</b> NSE pledge disclosures. <b>Gross NPA:</b> Screener.in quarterly results or the company's results.</li>
  <li><b>News:</b> Google News. <b>G-Sec yield:</b> CNBC's public quote for India's 10-year bond.</li>
  <li>Hover a figure to see its exact source and date. Nothing is estimated to fill a gap: missing data stays blank and the score shows how much of a test ran.</li></ul>

  <h3 id="h-metrics">12. The measures explained (short version)</h3>
  <ul><li><b>ROE</b> = net profit ÷ average shareholders' equity. <b>ROCE</b> = operating profit before interest and tax (EBIT) ÷ average (equity + borrowings).
  <b>ROIC</b> = EBIT after tax ÷ average (equity + borrowings − cash). Higher and steadier is better; above ~15% is good, above ~25% exceptional.</li>
  <li><b>P/E</b> = market cap ÷ net profit (last 4 quarters when newer than the annual report). <b>P/B</b> = market cap ÷ book value.</li>
  <li><b>Margin of safety</b> = how far the price is below the program's conservative value estimate.</li>
  <li>The PDF guide has every formula, threshold and weight.</li></ul>

  <h3 id="h-appearance">13. Themes, text size, sidebar</h3>
  <ul><li>Top right: <b>Theme</b> (Light blue, Night black &amp; white, Night black &amp; green) and <b>Text</b> size. The ☰ button collapses the sidebar to icons.
  These are remembered in this browser.</li></ul>

  <h3 id="h-trouble">14. Troubleshooting</h3>
  <ul><li><b>Nothing opens / "Port busy":</b> the screener is already running; open <kbd>http://127.0.0.1:8765</kbd>.</li>
  <li><b>No alerts:</b> alerts only cover shortlisted and held stocks; check both lists aren't empty.</li>
  <li><b>"Daily check hasn't run" notice:</b> the PC was off or asleep at 18:30, or the task was removed; run <b>Set up daily check.bat</b> again.</li>
  <li><b>A score says "not enough data":</b> hover the tests marked "no data" to see what's missing.</li></ul>

  <h3 id="h-cli">15. Commands (optional)</h3>
  <p class="muted">From the inner <kbd>india_screener_project</kbd> folder in a terminal:</p>
  <pre>py -m india_screener daily                     run the daily check now
py -m india_screener refresh-current-prices    today's NSE closing prices
py -m india_screener autofill --only financials   re-read all financials from Screener
py -m india_screener ledger --csv log.csv      export the recommendation log
py -m india_screener notify-test               test the Windows pop-up</pre>
  </section></div>`;
  main.querySelectorAll("[data-jump]").forEach(a => a.onclick = e => { e.preventDefault(); $("#h-" + a.dataset.jump)?.scrollIntoView({behavior: "smooth"}); });
}

// ---- notification centre
let noteKind = "";
async function notifications() {
  const d = await guard(() => api("/api/notifications" + (noteKind ? "?kind=" + noteKind : ""))); if (!d) return;
  const by = d.unread_by_kind || {};
  main.innerHTML = `<h2>Notifications</h2>
  <p class="lead">Everything the program has told you, in one place.
    <span class="note stock" style="display:inline;padding:.05rem .5rem;border-left-width:1px">Light blue</span> = a stock or news alert (the same ones sent as pop-ups, with full detail).
    <span class="note program" style="display:inline;padding:.05rem .5rem;border-left-width:1px">Light yellow</span> = about the program itself:
    a data source failed, a refresh finished, prices went stale, the daily check didn't run. Program notices are never sent as pop-ups.</p>
  <div class="tabs">${[["", "All"], ["stock", `Stock &amp; news${by.stock ? " (" + by.stock + " new)" : ""}`], ["program", `Program${by.program ? " (" + by.program + " new)" : ""}`]]
    .map(([k, l]) => `<button class="btn ${noteKind === k ? "" : "ghost"}" data-kind="${k}">${l}</button>`).join("")}
    <button class="btn ghost" id="readall" ${d.unread ? "" : "disabled"}>Mark all as read</button></div>
  ${d.items.length ? d.items.map(n => `<div class="note ${n.kind} ${n.read ? "" : "unread"}" data-id="${n.id}">
      <div class="nt">${esc(n.title)}</div>
      <div class="meta">${esc(n.created_at.slice(0, 16).replace("T", " "))} · ${n.kind === "stock" ? "Stock &amp; news" : "Program"}${n.level === "error" ? " · needs attention" : ""}
        ${n.symbol ? ` · <button class="link" data-an="${esc(n.symbol)}">open ${esc(n.symbol)}</button>` : ""}</div>
      ${n.body ? `<pre>${esc(n.body)}</pre>` : ""}</div>`).join("")
    : '<p class="muted">Nothing here yet. Stock alerts and program notices will appear as they happen.</p>'}`;
  main.querySelectorAll("[data-kind]").forEach(b => b.onclick = () => { noteKind = b.dataset.kind; notifications(); });
  $("#readall").onclick = () => guard(async () => { await api("/api/notifications/read", {}); refreshUnread(); notifications(); });
  const unreadIds = d.items.filter(n => !n.read).map(n => n.id);
  if (unreadIds.length) setTimeout(() => api("/api/notifications/read", {ids: unreadIds}).then(refreshUnread).catch(() => {}), 1500);
  bindAnalyseLinks();
}

route();
pollJob();
refreshUnread();
