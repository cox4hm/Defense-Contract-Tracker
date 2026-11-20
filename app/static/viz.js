/* Small, dependency-free chart helpers, following the dataviz mark specs:
   thin bars with a 4px rounded data end (square at the baseline), value at the tip,
   recessive hairline grid, one shared tooltip, text set with textContent. */
"use strict";

const Viz = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const nf = new Intl.NumberFormat("en-US");
  const num = (v) => (v === null || v === undefined || isNaN(v) ? "–" : nf.format(Math.round(Number(v))));
  const compact = (v) => {
    const n = Number(v);
    if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (Math.abs(n) >= 1e4) return Math.round(n / 1e3) + "K";
    return nf.format(n);
  };
  // $40.7B, $13.3M, $950K
  const money = (v) => {
    if (v === null || v === undefined || isNaN(v)) return "–";
    const n = Number(v), a = Math.abs(n);
    if (a >= 1e9) return "$" + (n / 1e9).toFixed(a >= 1e11 ? 0 : 1) + "B";
    if (a >= 1e6) return "$" + (n / 1e6).toFixed(a >= 1e8 ? 0 : 1) + "M";
    if (a >= 1e3) return "$" + Math.round(n / 1e3) + "K";
    return "$" + nf.format(Math.round(n));
  };

  /* ------------------------------------------------------------ tooltip */
  const tip = () => document.getElementById("tip");
  function showTip(evt, value, label, extra) {
    const t = tip();
    t.replaceChildren();
    const b = document.createElement("b");
    b.textContent = value;
    t.appendChild(b);
    for (const line of [label, extra].filter(Boolean)) {
      const s = document.createElement("div");
      s.textContent = line;
      t.appendChild(s);
    }
    t.hidden = false;
    let x, y;
    if (evt.clientX !== undefined && evt.type !== "focus") { x = evt.clientX; y = evt.clientY; }
    else { const r = evt.target.getBoundingClientRect(); x = r.left + r.width / 2; y = r.top; }
    const w = t.offsetWidth, h = t.offsetHeight;
    t.style.left = Math.min(window.innerWidth - w - 8, Math.max(8, x - w / 2)) + "px";
    t.style.top = (y - h - 12 < 8 ? y + 18 : y - h - 12) + "px";
  }
  function hideTip() { tip().hidden = true; }
  function attachTip(el, value, label, extra) {
    const show = (e) => showTip(e, value, label, extra);
    el.addEventListener("pointermove", show);
    el.addEventListener("pointerleave", hideTip);
    el.addEventListener("focus", show);
    el.addEventListener("blur", hideTip);
  }

  /* ------------------------------------------------------------ heat level */
  // 7 sequential steps on a log scale, so one giant disease doesn't wash out the rest
  function heatLevel(v, max) {
    if (!v) return 0;
    return Math.max(1, Math.min(7, 1 + Math.floor(6 * Math.log1p(v) / Math.log1p(Math.max(max, 1)))));
  }

  /* ------------------------------------------------------------ horizontal bars (HTML) */
  // items: [{label, value, note, key}] ; opts: {format, alt, onClick(key), pressed(key), empty}
  function hbars(el, items, opts = {}) {
    el.replaceChildren();
    const rows = items.filter((d) => d.value !== null && d.value !== undefined);
    if (!rows.length) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = opts.empty || "Nothing to show yet.";
      el.appendChild(p);
      return;
    }
    const max = opts.max || Math.max(...rows.map((d) => Number(d.value)), 1);
    const fmt = opts.format || num;
    const reserve = rows.some((d) => d.note) ? "12em" : "5em";   // room for the value label at the tip
    for (const d of rows) {
      const row = document.createElement(opts.onClick ? "button" : "div");
      row.className = "hb";
      if (opts.onClick) {
        row.type = "button";
        row.setAttribute("aria-pressed", String(opts.pressed ? opts.pressed(d.key) : false));
        row.addEventListener("click", () => opts.onClick(d.key));
      }
      const lab = document.createElement("span");
      lab.className = "lab";
      lab.textContent = d.label;
      lab.title = d.label;
      if (d.sub) {                       // a second, quieter line under the label
        const sm = document.createElement("small");
        sm.textContent = d.sub;
        lab.appendChild(sm);
      }
      const line = document.createElement("span");
      line.className = "barline";
      const bar = document.createElement("span");
      bar.className = "b" + (opts.alt ? " alt" : "");
      // leave room for the value label at the tip
      bar.style.width = `calc((100% - ${reserve}) * ${Number(d.value) / max})`;
      const val = document.createElement("span");
      val.className = "val";
      val.textContent = fmt(d.value);
      if (d.note) {
        const sm = document.createElement("small");
        sm.textContent = d.note;
        val.appendChild(sm);
      }
      line.append(bar, val);
      row.append(lab, line);
      el.appendChild(row);
    }
  }

  /* ------------------------------------------------------------ columns (SVG) */
  // opts: labels[], values[], tooltips[[value,label,extra]], faded[], tickEvery, height
  function columns(el, opts) {
    el.replaceChildren();
    const values = opts.values || [];
    if (!values.length) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = "Nothing to show yet.";
      el.appendChild(p);
      return;
    }
    const W = Math.max(el.clientWidth || 640, 300), H = opts.height || 220;
    const m = { t: 8, r: 4, b: 24, l: 36 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const rawMax = Math.max(...values, 1);
    const p10 = Math.pow(10, Math.floor(Math.log10(rawMax)));
    const max = opts.max || [1, 2, 4, 6, 8, 10].map((k) => k * p10).find((v) => v >= rawMax);   // quarters stay round
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", opts.aria || "column chart");
    const add = (tag, attrs, text) => {
      const n = document.createElementNS(NS, tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      if (text !== undefined) n.textContent = text;
      svg.appendChild(n);
      return n;
    };
    for (let i = 0; i <= 4; i++) {
      const y = m.t + ih - (ih * i) / 4;
      add("line", { x1: m.l, x2: W - m.r, y1: y, y2: y, class: i === 0 ? "base" : "grid" });
      add("text", { x: m.l - 6, y: y + 4, "text-anchor": "end" }, (opts.axisFormat || compact)((max * i) / 4));
    }
    const step = iw / values.length;
    const bw = Math.min(24, Math.max(2, step - 2));      // <= 24px, 2px surface gap
    values.forEach((v, i) => {
      const h = Math.max(0, (v / max) * ih);
      const x = m.l + i * step + (step - bw) / 2, y = m.t + ih - h, r = Math.min(4, bw / 2, h);
      // rounded data end on top, square at the baseline
      const d = `M${x},${m.t + ih} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${m.t + ih} Z`;
      const path = add("path", { d, class: "col" + (opts.faded && opts.faded[i] ? " faded" : ""), tabindex: "0" });
      // generous hit area: the whole slot
      const hit = add("rect", { x: m.l + i * step, y: m.t, width: step, height: ih, fill: "transparent" });
      const [tv, tl, te] = (opts.tooltips && opts.tooltips[i]) || [num(v), opts.labels[i]];
      attachTip(hit, tv, tl, te);
      attachTip(path, tv, tl, te);
      if (opts.labels[i]) add("text", { x: x + bw / 2, y: H - 6, "text-anchor": "middle" }, opts.labels[i]);
    });
    el.appendChild(svg);
  }

  return { num, compact, money, heatLevel, hbars, columns, attachTip, showTip, hideTip, NS };
})();
