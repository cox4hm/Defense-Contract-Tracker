/* Defense Contract Tracker: dashboard script (no framework, no chart library). */
"use strict";

const $ = (id) => document.getElementById(id);
const { money, num } = Viz;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const BRANCH_VAR = { "Navy": "--navy", "DLA": "--dla", "Army": "--army", "Air Force": "--af", "DISA": "--disa", "Other DoD": "--other" };
const BRANCH_NAME = { "DLA": "Defense Logistics Agency", "DISA": "Defense Information Systems Agency",
                      "Other DoD": "Other Defense agencies" };
const color = (branch) => `var(${BRANCH_VAR[branch] || "--other"})`;

const state = { window: 24, size: 0, comp: "", q: "", branches: new Set(Object.keys(BRANCH_VAR)),
                sort: "final_end", shown: 25 };
let DATA = null;

/* ------------------------------------------------------------------ text helpers */
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== false && v !== null && v !== undefined) n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.append(kid);
  return n;
}
const parseDate = (iso) => {
  if (!iso) return null;
  if (iso instanceof Date) return iso;
  if (iso.length > 10) return new Date(iso);            // a timestamp: the reader's own time zone
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
};
const monthYear = (iso) => { const d = parseDate(iso); return d ? `${MONTHS[d.getMonth()]} ${d.getFullYear()}` : "–"; };
const fullDate = (iso) => { const d = parseDate(iso); return d ? `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}` : "–"; };
function fromNow(days) {
  if (days === null || days === undefined) return "";
  if (days < 0) return "ended";
  if (days < 7) return "this week";
  if (days < 45) return `in ${days} days`;
  const m = Math.round(days / 30.4);
  return m < 24 ? `in ${m} months` : `in ${(days / 365).toFixed(1)} years`;
}
const ACRONYMS = new Set(["LLC", "LP", "LLP", "BAE", "HII", "CACI", "SAIC", "AECOM", "IT", "US", "USA", "JV", "II",
  "III", "IV", "DBA", "NV", "PC", "CGI", "GDIT", "KBR", "ADS", "MHI", "NNS", "GE", "BWXT", "L3HARRIS", "PAE", "AT&T", "SMS", "DLT"]);
const SMALL_WORDS = new Set(["OF", "AND", "THE", "FOR", "IN", "AT", "ON", "TO", "BY"]);
// "HUNTINGTON INGALLS INDUSTRIES, INC" -> "Huntington Ingalls Industries, Inc."
function nice(name) {
  if (!name) return "Unknown company";
  if (name !== name.toUpperCase()) return name;
  return name.toLowerCase().split(/(\s+|-|\/)/).map((w, i) => {
    const bare = w.replace(/[^a-z0-9&]/g, "").toUpperCase();
    if (ACRONYMS.has(bare) || (bare.length > 1 && bare.length <= 4 && !/[AEIOUY]/.test(bare))) return w.toUpperCase();
    if (bare === "INC" || bare === "CORP" || bare === "CO" || bare === "LTD") return w.charAt(0).toUpperCase() + w.slice(1).replace(/\.?$/, ".").replace(/,\./, ",");
    if (i > 0 && SMALL_WORDS.has(bare)) return w;
    return w.charAt(0).toUpperCase() + w.slice(1);
  }).join("").replace(/\.\./g, ".");
}
const KEEP = new Set(["USS", "USNS", "CVN", "DDG", "LPD", "LHD", "LHA", "SSN", "SSBN", "CG", "LCS", "ESB", "NAVSEA",
  "NAVFAC", "NAVSUP", "NAVAIR", "SPAWAR", "NAVWAR", "NIWC", "NSWC", "SUPSHIP", "MARMC", "HVAC", "IT", "DOD", "USN",
  "USMC", "USAF", "DLA", "DISA", "NASA", "JBLE", "NAS", "NSA", "NAB", "NNSY", "PSNS", "FFP", "IDIQ", "MATOC", "SATOC",
  "O&M", "R&D", "RDT&E", "C4I", "C5I", "ISR", "AV", "CIO", "ERP", "PM", "SOE", "UAS", "COVID", "II", "III", "IV", "JV"]);
// "IGF::OT::IGF USS SAN ANTONIO (LPD 17) DRYDOCK" -> "IGF::OT::IGF USS San antonio (LPD 17) drydock"
function sentence(s) {
  if (!s) return "";
  s = s.replace(/^IGF::\w+::IGF\s*/i, "");          // an internal accounting tag on many Navy descriptions
  if (s !== s.toUpperCase()) return s;
  let first = true;
  return s.split(/(\s+)/).map((w) => {
    if (/^\s+$/.test(w)) return w;
    const bare = w.replace(/[^A-Za-z0-9&]/g, "").toUpperCase();
    let out;
    if (KEEP.has(bare) || /\d/.test(w) || w.includes("::") || (bare.length >= 2 && bare.length <= 4 && !/[AEIOUY]/.test(bare))) out = w;
    else out = w.toLowerCase();
    if (first && out === w.toLowerCase()) out = out.charAt(0).toUpperCase() + out.slice(1);
    first = false;
    return out;
  }).join("");
}
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

/* ------------------------------------------------------------------ filters */
function matches(r) {
  if (r.days_left > state.window * 30.44) return false;
  if (!state.branches.has(r.branch)) return false;
  if (state.size && r.amount < state.size) return false;
  if (state.comp === "sole" && !r.sole_source) return false;
  if (state.comp === "competed" && (!r.has_detail || r.sole_source)) return false;
  if (state.comp === "setaside" && !r.set_aside_used) return false;
  if (state.q) {
    const hay = `${r.company} ${r.recipient_name} ${r.description} ${r.award_id} ${r.city}`.toLowerCase();
    if (!hay.includes(state.q)) return false;
  }
  return true;
}

/* ------------------------------------------------------------------ hero */
function renderHero(o) {
  const h = o.headline;
  const n = Number(h.runway_count || 0);
  const end = new Date(); end.setMonth(end.getMonth() + o.runway_months);
  if (!n) {
    $("headline").textContent = "No contract data yet.";
    $("subline").textContent = "The first download is running. It takes about 30 minutes; refresh the page to see progress.";
    return;
  }
  $("headline").replaceChildren(
    el("span", { class: "amt", text: money(h.runway_amount) }),
    `of Hampton Roads defense work comes up for renewal in the next ${o.runway_months === 24 ? "two years" : o.runway_months + " months"}.`);
  const parts = [`${num(n)} contracts reach their final end date, options included, by ${MONTHS[end.getMonth()]} ${end.getFullYear()}; ` +
                 `${num(h.next6_count)} of them (${money(h.next6_amount)}) within six months.`];
  if (Number(h.detailed) >= 20) {
    parts.push(`${Math.round(100 * h.sole_source / h.detailed)}% weren't competed the last time, and ` +
               `${Math.round(100 * h.set_aside / h.detailed)}% were set aside for small businesses.`);
  }
  $("subline").textContent = parts.join(" ");
  const run = o.last_run;
  const upd = run ? fullDate(run.finished_at) : "never";
  const pending = Number(h.details_pending);
  $("fresh").textContent = `Updated ${upd} from USAspending.gov. Defense contracts appear about ${Math.round(o.lag_days / 30)} months after signing.` +
    (pending ? ` Still reading ${num(pending)} contract pages; figures will sharpen as they arrive.` : "");
}

/* ------------------------------------------------------------------ branch chips */
function renderChips() {
  const box = $("branch-chips");
  box.replaceChildren();
  const counts = {};
  for (const r of DATA.runway) counts[r.branch] = (counts[r.branch] || 0) + 1;
  for (const b of Object.keys(BRANCH_VAR)) {
    if (!counts[b]) continue;
    const chip = el("button", { type: "button", class: "chip", "aria-pressed": String(state.branches.has(b)),
                                title: BRANCH_NAME[b] || b },
      el("span", { class: "dot", style: `background:${color(b)}` }), b, el("span", { class: "n", text: num(counts[b]) }));
    chip.addEventListener("click", () => {
      // first click isolates a branch; clicking the only selected one shows them all again
      const all = state.branches.size === Object.keys(BRANCH_VAR).length;
      if (all) state.branches = new Set([b]);
      else if (state.branches.has(b) && state.branches.size === 1) state.branches = new Set(Object.keys(BRANCH_VAR));
      else state.branches.has(b) ? state.branches.delete(b) : state.branches.add(b);
      update();
    });
    box.appendChild(chip);
  }
}

/* ------------------------------------------------------------------ the runway strip */
let STRIP = null;   // { circles: [[circle, row]], scale }
function radiusScale(maxAmt, k) { return (v) => Math.max(3, 24 * k * Math.sqrt(Math.max(v, 0) / maxAmt)); }

function renderStrip() {
  const box = $("strip");
  box.replaceChildren();
  const rows = DATA.runway;
  if (!rows.length) return;
  const W = Math.max(box.clientWidth || 900, 320);
  const small = W < 600;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const end = new Date(today); end.setMonth(end.getMonth() + DATA.runway_months);
  const m = { l: 6, r: 6 };
  const x = (d) => m.l + (W - m.l - m.r) * (d - today) / (end - today);
  const maxAmt = Math.max(...rows.map((r) => Number(r.amount) || 0), 1);

  // beeswarm: biggest first, each circle as close to the center line as it fits
  let k = 1, placed, extent;
  for (let attempt = 0; attempt < 6; attempt++) {
    const R = radiusScale(maxAmt, k * (small ? 0.75 : 1));
    placed = []; extent = 0;
    const order = [...rows].sort((a, b) => b.amount - a.amount);
    for (const r of order) {
      const cx = x(parseDate(r.final_end)), rad = R(r.amount);
      const near = placed.filter((p) => Math.abs(p.cx - cx) < p.r + rad + 1);
      let y = 0;
      for (let step = 0; step < 400; step++) {
        const cand = step === 0 ? 0 : (step % 2 ? 1 : -1) * Math.ceil(step / 2) * 1.5;
        if (near.every((p) => (p.cx - cx) ** 2 + (p.cy - cand) ** 2 >= (p.r + rad + 0.8) ** 2)) { y = cand; break; }
      }
      placed.push({ cx, cy: y, r: rad, row: r });
      extent = Math.max(extent, Math.abs(y) + rad);
    }
    if (extent <= (small ? 120 : 170)) break;
    k *= 0.82;
  }
  const top = 34, mid = top + extent + 4, H = mid + extent + 34;
  const svg = document.createElementNS(Viz.NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const add = (parent, tag, attrs, text) => {
    const n = document.createElementNS(Viz.NS, tag);
    for (const [a, v] of Object.entries(attrs)) n.setAttribute(a, v);
    if (text !== undefined) n.textContent = text;
    parent.appendChild(n);
    return n;
  };
  // fiscal years begin October 1
  const g = add(svg, "g", { class: "fy" });
  for (let d = new Date(today.getFullYear(), 9, 1); d <= end; d.setFullYear(d.getFullYear() + 1)) {
    if (d <= today) continue;
    const xx = x(d);
    add(g, "line", { x1: xx, x2: xx, y1: 22, y2: H - 22, class: "fyline" });
    const label = small ? `FY${String(d.getFullYear() + 1).slice(2)}` : `Fiscal ${d.getFullYear() + 1} begins`;
    const nearEdge = xx > W - (small ? 40 : 150);
    add(g, "text", { x: nearEdge ? xx - 5 : xx + 5, y: 16, "text-anchor": nearEdge ? "end" : "start" }, label);
  }
  add(svg, "line", { x1: m.l, x2: W - m.r, y1: H - 22, y2: H - 22, class: "axis" });
  const every = small ? 6 : 3;
  for (let i = 0; i <= DATA.runway_months; i += 1) {
    const d = new Date(today.getFullYear(), today.getMonth() + i, 1);
    if (d < today || d > end) continue;
    const xx = x(d);
    add(svg, "line", { x1: xx, x2: xx, y1: H - 22, y2: H - 18, class: "axis" });
    if (d.getMonth() % every === 0) {
      add(svg, "text", { x: xx, y: H - 4, "text-anchor": "middle" },
          d.getMonth() === 0 ? String(d.getFullYear()) : MONTHS[d.getMonth()]);
    }
  }
  const t = add(svg, "g", { class: "today" });
  add(t, "line", { x1: m.l, x2: m.l, y1: 22, y2: H - 22 });
  add(t, "text", { x: m.l + 5, y: 16 }, "Today");

  const circles = [];
  // draw small on top of big so nothing hides
  for (const p of [...placed].sort((a, b) => b.r - a.r)) {
    const c = add(svg, "circle", { cx: p.cx.toFixed(1), cy: (mid + p.cy).toFixed(1), r: p.r.toFixed(1),
                                   fill: color(p.row.branch) });
    Viz.attachTip(c, nice(p.row.company), `${money(p.row.amount)} · ${p.row.branch}`,
                  `Final end ${monthYear(p.row.final_end)} (${fromNow(p.row.days_left)})`);
    c.addEventListener("click", () => openAward(p.row.id));
    circles.push([c, p.row]);
  }
  box.appendChild(svg);
  STRIP = { circles, R: radiusScale(maxAmt, k * (small ? 0.75 : 1)) };
  // size key, same scale as the circles
  const key = $("sizekey");
  key.replaceChildren("Size:");
  for (const v of [1e7, 1e8, 1e9]) {
    if (v > maxAmt * 1.5) continue;
    const r = STRIP.R(v);
    key.append(el("i", { style: `width:${2 * r}px;height:${2 * r}px` }), money(v));
  }
}

/* ------------------------------------------------------------------ the table */
function lastTime(r) {
  if (!r.has_detail) return el("span", { class: "muted", text: "Not read yet" });
  const kids = [];
  kids.push(el("span", { class: "tag" + (r.sole_source ? " strong" : ""),
                         text: r.sole_source ? "Not competed" : (r.offers ? `Competed · ${r.offers} bid${r.offers === 1 ? "" : "s"}` : "Competed") }));
  if (r.set_aside_used) kids.push(el("span", { class: "tag", text: "Set-aside" }));
  return el("span", {}, kids);
}

function renderTable(rows) {
  const tb = document.querySelector("#runway-table tbody");
  tb.replaceChildren();
  const sorted = [...rows].sort(state.sort === "amount" ? (a, b) => b.amount - a.amount
                                                         : (a, b) => (a.final_end < b.final_end ? -1 : a.final_end > b.final_end ? 1 : b.amount - a.amount));
  for (const r of sorted.slice(0, state.shown)) {
    const tr = el("tr", { class: "click", tabindex: "0" },
      el("td", { class: "when nowrap" }, monthYear(r.final_end), el("small", { text: fromNow(r.days_left) })),
      el("td", {}, el("span", { class: "co", text: nice(r.company) }),
         el("span", { class: "desc", text: sentence(r.description) || r.award_id })),
      el("td", { class: "hide-sm nowrap" }, el("span", { class: "dot", style: `background:${color(r.branch)}` }), " ", r.branch),
      el("td", { class: "r num nowrap", text: money(r.amount) }),
      el("td", { class: "hide-sm" }, lastTime(r)));
    tr.addEventListener("click", () => openAward(r.id));
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openAward(r.id); } });
    tb.appendChild(tr);
  }
  if (!rows.length) tb.appendChild(el("tr", {}, el("td", { colspan: "5", class: "empty", text: "No contracts match these filters." })));
  const total = rows.reduce((s, r) => s + Number(r.amount || 0), 0);
  $("table-note").textContent = `Showing ${num(Math.min(state.shown, rows.length))} of ${num(rows.length)} contracts (${money(total)} obligated).`;
  $("more").hidden = rows.length <= state.shown;
}

function update() {
  const rows = DATA.runway.filter(matches);
  if (STRIP) for (const [c, r] of STRIP.circles) c.classList.toggle("dim", !matches(r));
  for (const chip of $("branch-chips").children) {
    const b = chip.childNodes[1].textContent;
    chip.setAttribute("aria-pressed", String(state.branches.has(b)));
  }
  renderTable(rows);
}

function wireFilters() {
  for (const b of $("f-window").querySelectorAll("button")) {
    b.addEventListener("click", () => {
      state.window = Number(b.dataset.v);
      for (const o of $("f-window").querySelectorAll("button")) o.setAttribute("aria-pressed", String(o === b));
      update();
    });
  }
  $("f-size").addEventListener("change", (e) => { state.size = Number(e.target.value); update(); });
  $("f-comp").addEventListener("change", (e) => { state.comp = e.target.value; update(); });
  let t;
  $("f-q").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { state.q = e.target.value.trim().toLowerCase(); update(); }, 150); });
  $("more").addEventListener("click", () => { state.shown += 25; update(); });
  for (const b of document.querySelectorAll("#runway-table th button")) {
    b.addEventListener("click", () => {
      state.sort = b.dataset.sort;
      for (const o of document.querySelectorAll("#runway-table th button")) o.removeAttribute("aria-sort");
      b.setAttribute("aria-sort", state.sort === "amount" ? "descending" : "ascending");
      update();
    });
  }
}

/* ------------------------------------------------------------------ drawer */
const drawer = () => $("drawer");
function openDrawer(kids, hash) {
  const inner = $("d-inner");
  inner.replaceChildren(...kids.filter((k) => k !== null && k !== undefined && k !== false));
  if (!drawer().open) drawer().showModal();
  inner.scrollTop = 0;
  drawer().scrollTop = 0;
  if (hash) history.replaceState(null, "", "#" + hash);
}
function closeBtn() {
  return el("button", { type: "button", class: "close", text: "Close", onclick: () => drawer().close() });
}
function fact(label, value, note) {
  return el("div", {}, el("dt", { text: label }), el("dd", {}, value ?? "–", note ? el("small", { text: note }) : null));
}

function lifeline(a) {
  // start -> today -> current end -> final end (options)
  const s = parseDate(a.start_date), e = parseDate(a.end_date), p = parseDate(a.potential_end_date);
  if (!s || !e) return null;
  const last = p && p > e ? p : e, now = new Date();
  const lo = s, hi = new Date(Math.max(last, now));
  const W = 560, H = 54, x = (d) => 6 + (W - 12) * (d - lo) / Math.max(hi - lo, 1);
  const svg = document.createElementNS(Viz.NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Runs from ${fullDate(a.start_date)} to ${fullDate(a.end_date)}` +
                   (p && p > e ? `, with options to ${fullDate(a.potential_end_date)}` : ""));
  const add = (tag, at, txt) => { const n = document.createElementNS(Viz.NS, tag); for (const [k, v] of Object.entries(at)) n.setAttribute(k, v); if (txt) n.textContent = txt; svg.appendChild(n); };
  add("rect", { x: x(s), y: 14, width: Math.max(x(e) - x(s), 2), height: 10, rx: 2, class: "span" });
  if (p && p > e) add("rect", { x: x(e) + 2, y: 14, width: Math.max(x(p) - x(e) - 2, 2), height: 10, rx: 2, class: "opt" });
  add("line", { x1: x(now), x2: x(now), y1: 6, y2: 32, class: "now" });
  add("text", { x: x(s), y: 46 }, String(s.getFullYear()));
  add("text", { x: x(last), y: 46, "text-anchor": "end" }, String(last.getFullYear()));
  const lab = document.createElement("div");
  lab.className = "life";
  lab.append(svg, el("div", { class: "ramp" },
    el("i", { style: "background:var(--s4)" }), "Current period",
    p && p > e ? el("i", { style: "background:var(--s2)" }) : null, p && p > e ? "Options not yet used" : null,
    el("i", { style: "background:var(--today);width:3px" }), "Today"));
  return lab;
}

function miniList(rows, onPick, right = (r) => money(r.amount)) {
  if (!rows || !rows.length) return el("p", { class: "empty", text: "None." });
  return el("ul", { class: "mini" }, rows.map((r) => el("li", {},
    el("button", { type: "button", onclick: () => onPick(r) },
      el("span", {}, sentence(r.description) || r.award_id || nice(r.name),
         el("small", { text: r.final_end ? `${r.branch || ""} · final end ${monthYear(r.final_end)}` : (r.awards ? `${num(r.awards)} contracts` : "") })),
      el("b", { class: "num", text: right(r) })))));
}

async function openAward(id) {
  let a;
  try { a = await getJSON("/api/award/" + encodeURIComponent(id)); }
  catch { return; }
  const comp = !a.has_detail ? "Not read yet"
    : a.sole_source ? "Not competed" : (a.competed ? sentence(a.competed) : "Not stated");
  openDrawer([
    el("div", { class: "d-top" },
      el("span", { class: "kind" }, el("span", { class: "dot", style: `background:${color(a.branch)}` }),
         `${BRANCH_NAME[a.branch] || a.branch} contract`),
      closeBtn()),
    el("h2", { id: "d-title", text: nice(a.company) }),
    el("p", { class: "lede", text: sentence(a.description) }),
    lifeline(a),
    el("dl", { class: "facts" },
      fact("Obligated so far", money(a.amount), Number(a.outlays) > 0 ? `${money(a.outlays)} paid out` : null),
      fact("Value with all options", money(a.base_and_all_options), a.has_detail ? null : "Not read yet"),
      fact("Final end date", fullDate(a.final_end), a.potential_end_date && a.potential_end_date > a.end_date
           ? `Current period ends ${fullDate(a.end_date)}` : fromNow(Math.round((parseDate(a.final_end) - new Date()) / 864e5))),
      fact("Last time", comp, a.offers ? `${a.offers} bid${a.offers === 1 ? "" : "s"} received` : null),
      fact("Set-aside", a.set_aside ? sentence(a.set_aside.replace(/\.$/, "")) : (a.has_detail ? "None stated" : "–")),
      fact("Pricing", a.pricing ? sentence(a.pricing) : "–"),
      fact("Buying office", a.awarding_office ? nice(a.awarding_office) : (a.awarding_subagency || "–")),
      fact("Work performed in", a.locality || nice(a.city || "") || "–"),
      fact("Industry", a.naics_desc ? sentence(a.naics_desc) : "–", a.sector),
      fact("What's bought", a.psc_desc ? sentence(a.psc_desc) : "–"),
      fact("Contract number", a.award_id),
      fact("Holder", nice(a.recipient_name), a.small_business === true ? "Small business" : a.small_business === false ? "Not a small business" : null)),
    el("p", {}, el("a", { href: a.link, target: "_blank", rel: "noopener", text: "Open on USAspending.gov" }),
       " · ", el("button", { type: "button", class: "btn", text: `${nice(a.company)} profile`, onclick: () => openCompany(a.company_key) })),
    a.company_others && a.company_others.length ? el("h3", { text: "This company's other contracts ending soon" }) : null,
    a.company_others && a.company_others.length ? miniList(a.company_others, (r) => openAward(r.id)) : null,
  ], "award=" + encodeURIComponent(id));
}

async function openCompany(key) {
  let c;
  try { c = await getJSON("/api/company/" + encodeURIComponent(key)); }
  catch { return; }
  const branchBox = el("div");
  openDrawer([
    el("div", { class: "d-top" }, el("span", { class: "kind", text: "Company profile · Hampton Roads work" }), closeBtn()),
    el("h2", { id: "d-title", text: nice(c.company) }),
    el("p", { class: "lede", text: [c.main_industry ? sentence(c.main_industry) : null,
                                    c.main_city ? `mostly in ${nice(c.main_city)}` : null,
                                    c.small_business ? "small business" : null].filter(Boolean).join(" · ") }),
    el("div", { class: "big3" },
      el("div", {}, el("div", { class: "figure", text: money(c.amount) }), el("small", { text: "obligated on contracts active in the last five years" })),
      el("div", {}, el("div", { class: "figure", text: num(c.awards) }), el("small", { text: "contracts of $1M and up" })),
      el("div", {}, el("div", { class: "figure", text: money(c.ending_amount) }), el("small", { text: `on ${num(c.ending)} contracts ending in the next two years` }))),
    el("h3", { text: "Who buys from them" }), branchBox,
    c.subsidiaries.length > 1 ? el("h3", { text: "Registered as" }) : null,
    c.subsidiaries.length > 1 ? miniList(c.subsidiaries, () => {}, (r) => money(r.amount)) : null,
    el("h3", { text: "Contracts ending in the next two years" }),
    miniList(c.ending_list, (r) => openAward(r.id)),
    el("h3", { text: "Largest contracts" }),
    miniList(c.largest, (r) => openAward(r.id)),
  ], "company=" + encodeURIComponent(key));
  Viz.hbars(branchBox, c.by_branch.map((b) => ({ label: BRANCH_NAME[b.branch] || b.branch, value: b.amount,
                                                note: `${num(b.awards)} contracts` })), { format: money });
}

/* ------------------------------------------------------------------ what changed */
const KIND = { new_award: "New contract", extended: "Extended", value_up: "Grew" };
function renderFeed(events) {
  const ul = $("feed");
  ul.replaceChildren();
  if (!events.length) {
    ul.appendChild(el("li", { style: "display:block" }, el("span", { class: "empty", text: "Changes show up here starting with the second daily update." })));
    return;
  }
  for (const e of events) {
    ul.appendChild(el("li", {},
      el("span", { class: "k" }, KIND[e.kind] || e.kind, el("br"), fullDate(e.created_at)),
      el("button", { type: "button", onclick: () => e.generated_id && openAward(e.generated_id) },
         el("b", { text: e.who ? e.title.split(e.who).join(nice(e.who)) : e.title }),
         el("span", { class: "d", text: sentence(e.detail || "") }))));
  }
}

/* ------------------------------------------------------------------ companies */
function renderCompanies(list) {
  const box = $("co-list");
  box.replaceChildren();
  if (!list.length) { box.appendChild(el("p", { class: "empty", text: "No companies loaded yet." })); return; }
  list.slice(0, 25).forEach((c, i) => {
    const row = el("button", { type: "button", class: "co-row" },
      el("span", { class: "rank", text: String(i + 1) }),
      el("span", { class: "nm" }, nice(c.company),
         el("small", { text: [(c.branches || []).join(", "), c.main_industry ? sentence(c.main_industry) : null].filter(Boolean).join(" · ") })),
      el("span", {},
         el("span", { class: "bars", "aria-hidden": "true" }, el("i", { style: `width:${Math.min(100, 100 * c.ending_amount / Math.max(c.amount, 1))}%` })),
         el("span", { class: "share", text: `${Math.round(100 * c.ending_amount / Math.max(c.amount, 1))}% ending in two years` })),
      el("span", { class: "fig" }, money(c.amount), el("small", { text: Number(c.ending_amount) ? `${money(c.ending_amount)} ending` : "none ending" })));
    row.addEventListener("click", () => openCompany(c.key));
    box.appendChild(row);
  });
}

/* ------------------------------------------------------------------ follow the money (Sankey) */
function renderFlow(f) {
  const box = $("flow");
  box.replaceChildren();
  if (!f.links.length) { box.appendChild(el("p", { class: "empty", text: "Nothing to show yet." })); return; }
  $("flow-intro").textContent = `${money(f.total)} obligated on ${num(f.companies)} companies' contracts that are still running, ` +
    "from the branch that buys, to the kind of work, to the company that does it. Hover a band for its amount.";
  const W = Math.max(box.clientWidth, 760), H = 560;
  const cols = { b: 0, s: 1, c: 2 };
  const nodes = new Map();
  const node = (id) => nodes.get(id) || nodes.set(id, { id, col: cols[id[0]], name: id.slice(2), in: 0, out: 0 }).get(id);
  for (const l of f.links) { node(l.source).out += l.value; node(l.target).in += l.value; }
  const colNodes = [[], [], []];
  for (const n of nodes.values()) { n.value = Math.max(n.in, n.out); colNodes[n.col].push(n); }
  const order = (a, b) => (a.name.startsWith("All other") ? 1 : b.name.startsWith("All other") ? -1 : b.value - a.value);
  colNodes.forEach((c) => c.sort(order));
  const pad = 8, top = 24;
  const ky = Math.min(...colNodes.map((c) => (H - top - pad * (c.length - 1)) / c.reduce((s, n) => s + n.value, 0)));
  const xs = [140, W * 0.45, W - 270], nw = 10;
  colNodes.forEach((c, ci) => { let y = top; for (const n of c) { n.x = xs[ci]; n.y = y; n.h = Math.max(n.value * ky, 1.5); y += n.h + pad; n.oy = n.y; n.iy = n.y; } });
  const svg = document.createElementNS(Viz.NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Flow of contract dollars from military branch to industry to company");
  const add = (p, tag, at, txt) => { const n = document.createElementNS(Viz.NS, tag); for (const [k, v] of Object.entries(at)) n.setAttribute(k, v); if (txt !== undefined) n.textContent = txt; p.appendChild(n); return n; };
  for (const [i, t] of ["Who buys", "Kind of work", "Who does it"].entries()) {
    add(svg, "text", { x: xs[i] + (i === 0 ? -8 : nw + 8) - (i === 2 ? 0 : 0), y: 12, "text-anchor": i === 0 ? "end" : "start", style: "fill:var(--muted);font-size:12px" }, t);
  }
  const links = [...f.links].sort((a, b) => order(nodes.get(a.target), nodes.get(b.target)));
  for (const l of links) {
    const s = nodes.get(l.source), t = nodes.get(l.target), w = l.value * ky;
    const y0 = s.oy + w / 2, y1 = t.iy + w / 2; s.oy += w; t.iy += w;
    const x0 = s.x + nw, x1 = t.x, mx = (x0 + x1) / 2;
    const p = add(svg, "path", { d: `M${x0},${y0} C${mx},${y0} ${mx},${y1} ${x1},${y1}`, "stroke-width": Math.max(w, 1),
                                 class: s.col === 0 ? "s1" : "s2", style: s.col === 0 ? `stroke:${color(s.name)}` : "" });
    Viz.attachTip(p, money(l.value), `${s.name} → ${t.col === 2 ? nice(t.name) : t.name}`);
  }
  const short = (t, n) => (t.length > n ? t.slice(0, n - 1) + "…" : t);
  colNodes.forEach((c) => {
    let lastY = -Infinity;
    for (const n of c) {
      const g = add(svg, "g", { class: "node" });
      const r = add(g, "rect", { x: n.x, y: n.y, width: nw, height: n.h, style: n.col === 0 ? `fill:${color(n.name)}` : "" });
      const name = n.col === 2 ? nice(n.name) : n.name;
      Viz.attachTip(r, money(n.value), name);
      // labels sit at the node's middle; skip one that would collide with the label above (the tooltip still has it)
      const y = Math.max(n.y + n.h / 2 + 4, lastY + 15);
      if (y > n.y + n.h + 10) continue;
      lastY = y;
      const left = n.col === 0;
      const tx = add(g, "text", { x: left ? n.x - 8 : n.x + nw + 6, y, "text-anchor": left ? "end" : "start" });
      add(tx, "tspan", {}, short(name, n.col === 2 ? 30 : 34));
      add(tx, "tspan", { class: "v", dx: 6 }, money(n.value));
    }
  });
  const wrap = el("div", { style: "overflow-x:auto" });
  wrap.appendChild(svg);
  box.appendChild(wrap);
}

/* ------------------------------------------------------------------ fiscal calendar */
function renderCalendar(m) {
  const cal = $("cal");
  cal.replaceChildren();
  const rows = m.monthly;
  if (!rows.length) { cal.appendChild(el("p", { class: "empty", text: "Nothing to show yet." })); return; }
  const fys = [...new Set(rows.map((r) => r.fiscal_year))].sort();
  const byKey = new Map(rows.map((r) => [r.month.slice(0, 7), r]));
  const complete = rows.filter((r) => r.complete);
  const max = Math.max(...complete.map((r) => Number(r.amount)), 1);
  const order = [9, 10, 11, 0, 1, 2, 3, 4, 5, 6, 7, 8];   // Oct .. Sep
  cal.appendChild(el("span", { class: "h", role: "columnheader", text: "" }));
  for (const mi of order) cal.appendChild(el("span", { class: "h" + (mi === 8 ? " sep" : ""), role: "columnheader", text: MONTHS[mi] }));
  const today = new Date();
  for (const fy of fys) {
    cal.appendChild(el("span", { class: "y", role: "rowheader", text: `FY${String(fy).slice(2)}` }));
    for (const mi of order) {
      const year = mi >= 9 ? fy - 1 : fy;
      const key = `${year}-${String(mi + 1).padStart(2, "0")}`;
      const r = byKey.get(key);
      const d = new Date(year, mi, 1);
      const cell = el("span", { class: "cell", role: "cell", tabindex: r ? "0" : null });
      if (d > today) cell.classList.add("future");
      else if (r && !r.complete) cell.classList.add("pending");
      else if (r) {
        const lvl = Math.max(1, Math.min(7, Math.ceil(7 * Number(r.amount) / max)));
        cell.style.background = `var(--s${lvl})`;
      }
      if (r && d <= today) {
        Viz.attachTip(cell, money(r.amount), `${MONTHS[mi]} ${year} (fiscal ${fy})`, r.complete ? null : "Still filling in");
        cell.setAttribute("aria-label", `${MONTHS[mi]} ${year}: ${money(r.amount)}${r.complete ? "" : ", still filling in"}`);
      }
      cal.appendChild(cell);
    }
  }
  const ramp = $("cal-ramp");
  ramp.replaceChildren("Less", ...[1, 2, 3, 4, 5, 6, 7].map((i) => el("i", { style: `background:var(--s${i})` })), "More",
                       el("i", { class: "pend", style: "margin-left:14px" }), "Still filling in");
  // the September rush, from complete fiscal years
  const sep = complete.filter((r) => r.month.slice(5, 7) === "09").map((r) => Number(r.amount));
  const other = complete.filter((r) => r.month.slice(5, 7) !== "09").map((r) => Number(r.amount));
  if (sep.length && other.length) {
    const ratio = (sep.reduce((a, b) => a + b) / sep.length) / (other.reduce((a, b) => a + b) / other.length);
    $("sep-callout").textContent = ratio >= 1.15
      ? `September brings ${ratio.toFixed(1)}× a typical month's contract dollars.`
      : "Here, September isn't much busier than other months.";
  }
}

/* ------------------------------------------------------------------ small business */
function renderSmall(m) {
  const fy = m.fy_totals.filter((r) => r.region && r.small_business !== null);
  const last = fy.filter((r) => r.fiscal_year <= m.fiscal_year).slice(-1)[0];
  if (last) {
    $("small-intro").textContent = `In fiscal ${last.fiscal_year}, small businesses received ${money(last.small_business)} of the ` +
      `${money(last.region)} in Defense contracts performed in Hampton Roads (${Math.round(100 * last.small_business / last.region)}%). ` +
      "The federal government's goal is 23% nationwide.";
  }
  Viz.columns($("sb-share"), {
    values: fy.map((r) => 100 * r.small_business / r.region),
    labels: fy.map((r) => `FY${String(r.fiscal_year).slice(2)}`),
    faded: fy.map((r) => r.fiscal_year > m.fiscal_year),
    tooltips: fy.map((r) => [`${(100 * r.small_business / r.region).toFixed(1)}%`, `Fiscal ${r.fiscal_year}`,
                             `${money(r.small_business)} of ${money(r.region)}${r.fiscal_year > m.fiscal_year ? " · still filling in" : ""}`]),
    axisFormat: (v) => `${Math.round(v)}%`, height: 220, aria: "Small business share by fiscal year",
    max: Math.max(20, Math.ceil(Math.max(...fy.map((r) => 100 * r.small_business / r.region), 1) / 20) * 20),
  });
  $("sb-fy").textContent = `Fiscal ${m.fiscal_year}, Hampton Roads work, by dollars obligated.`;
  Viz.hbars($("sb-top"), (m.categories.sb_recipient || []).slice(0, 10).map((r) => ({ label: nice(r.name), value: r.amount })),
            { format: money });
  Viz.hbars($("competition"), m.competition.map((r) => ({ label: r.name, value: r.awards, note: money(r.amount) })),
            { format: (v) => `${num(v)} contracts`, empty: "Contract pages are still being read." });
  Viz.hbars($("setasides"), m.set_asides.filter((r) => r.name !== "None")
            .map((r) => ({ label: r.name, value: r.awards, note: money(r.amount) })),
            { format: (v) => `${num(v)} contracts`, alt: true, empty: "None among the contracts read so far." });
}

/* ------------------------------------------------------------------ kits */
function renderKits(k) {
  const sm = k.summary || {};
  const stats = $("kit-stats");
  stats.replaceChildren();
  if (!Number(sm.contracts)) {
    stats.appendChild(el("p", { class: "empty", text: "Kit-program data hasn't loaded yet; it arrives with the next update." }));
  } else {
    stats.append(
      el("div", {}, el("div", { class: "figure", text: money(sm.obligated) }), el("small", { text: "obligated through the program since 2021" })),
      el("div", {}, el("div", { class: "figure", text: num(sm.orders) }), el("small", { text: `orders under ${num(sm.contracts)} master contracts held by ${num(sm.holders)} companies` })),
      el("div", {}, el("div", { class: "figure", text: monthYear(sm.order_end) }), el("small", { text: "the last month orders can be placed under these master contracts" })));
  }
  Viz.hbars($("kit-holders"), k.holders.map((h) => ({ label: nice(h.holder), value: h.obligated,
                                                     sub: `${num(h.orders)} orders · ${num(h.contracts)} master contract${h.contracts > 1 ? "s" : ""}` })),
            { format: money });
  const ul = $("kit-orders");
  ul.replaceChildren();
  for (const o of k.orders) {
    ul.appendChild(el("li", {}, el("div", {},
      el("span", {}, sentence((o.description || "").replace(/^\d+!/, "")) || o.piid,
         el("small", { text: `${nice(o.holder)} · ${monthYear(o.start_date)} · ${o.piid}` })),
      el("b", { class: "num", text: money(o.amount) }))));
  }
  if (!k.orders.length) ul.appendChild(el("li", { class: "empty", text: "No orders loaded yet." }));
  const t = $("kit-suppliers");
  t.replaceChildren(el("thead", {}, el("tr", {}, el("th", { text: "Supplier" }), el("th", { text: "Buys for" }),
                                         el("th", { class: "r", text: "Subcontracts" }), el("th", { class: "r", text: "Amount" }))));
  const tb = el("tbody");
  for (const s of k.suppliers) {
    tb.appendChild(el("tr", {}, el("td", { text: nice(s.sub_name) }), el("td", { class: "muted", text: nice(s.prime_recipient) }),
                      el("td", { class: "r", text: num(s.subawards) }), el("td", { class: "r num", text: money(s.amount) })));
  }
  if (!k.suppliers.length) {
    tb.appendChild(el("tr", {}, el("td", { colspan: "4", class: "empty",
      text: Number(sm.contracts) ? "None reported. Primes only have to report subcontracts of $30,000 or more, and on this program's largest orders they reported none."
                                 : "No supplier data yet." })));
  }
  t.appendChild(tb);
}

/* ------------------------------------------------------------------ watchlist */
function renderWatch(list) {
  const box = $("watchlist");
  box.replaceChildren();
  for (const c of list) {
    const found = Number(c.award_count || 0);
    box.appendChild(el("article", {},
      el("h3", { text: c.display_name }),
      el("p", { class: "meta", text: c.checked_at === null ? "Not checked yet."
        : found ? `${num(found)} Defense contracts in the last five years · largest ${Math.min(found, 50)} total ${money(c.total_amount)}`
                : "No Defense prime contracts found under this name or ID. The company may work as a subcontractor, sell through other companies, or be registered under a different ID." }),
      c.note ? el("p", { class: "small muted", text: c.note }) : null,
      el("ul", {}, c.awards.map((a) => el("li", {},
        el("span", { text: `${sentence(a.description) || a.award_id}` }),
        el("b", { class: "num nowrap", text: money(a.amount) }))))));
  }
}

/* ------------------------------------------------------------------ start */
async function main() {
  wireFilters();
  drawer().addEventListener("close", () => history.replaceState(null, "", location.pathname + location.search));
  drawer().addEventListener("click", (e) => { if (e.target === drawer()) drawer().close(); });
  try {
    DATA = await getJSON("/api/overview");
  } catch (e) {
    $("headline").textContent = "The data service isn't answering.";
    $("subline").textContent = "Check that the database is running, then reload the page.";
    return;
  }
  renderHero(DATA);
  renderChips();
  renderStrip();
  update();
  renderFeed(DATA.events);
  const run = DATA.last_run;
  if (run) $("run-info").textContent = `Last update: run ${run.id} (${run.mode}), ${run.status}, ${num(run.rows_written)} records.`;
  let w = window.innerWidth;
  window.addEventListener("resize", () => { if (Math.abs(window.innerWidth - w) > 40) { w = window.innerWidth; renderStrip(); update(); } });

  const h = new URLSearchParams(location.hash.slice(1));
  if (h.get("award")) openAward(h.get("award"));
  else if (h.get("company")) openCompany(h.get("company"));

  const jobs = [
    getJSON("/api/companies").then(renderCompanies),
    getJSON("/api/flow").then(renderFlow),
    getJSON("/api/money").then((m) => { renderCalendar(m); renderSmall(m); }),
    getJSON("/api/kits").then(renderKits),
    getJSON("/api/watchlist").then(renderWatch),
  ];
  for (const r of await Promise.allSettled(jobs)) if (r.status === "rejected") console.warn(r.reason);
}
main();
