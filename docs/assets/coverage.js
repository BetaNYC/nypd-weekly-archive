/* Coverage chart for one series page: which weeks of each precinct's file this archive
   holds. Reads data/<series>-coverage.json (written by `uv run nypd-archive site`).

   The page sets window.SERIES before this script runs:
     { key: "compstat", name: "CompStat",
       folders: [{ href, text }],           // where the CSV files are
       nypdFile: "This week's CompStat spreadsheet",   // label for the precinct's live file
       unit: { one, many, page } }   // optional: what a row is, for a report with one file for
                                     // the whole city (default: precinct, precincts, and
                                     // "The precinct's page on nyc.gov")

   and provides these elements: #pick, #chart, #detail (its own section under the chart,
   headed by #detail-h, holding a short "pick one" note until a row is chosen), #table,
   #tip, #built, and any [data-total] or [data-calc] tile. A tile's data-total names a key of the file's totals;
   data-calc is one of weeks, span, held, held_share, estimate. */
document.addEventListener("DOMContentLoaded", async () => {
  const S = window.SERIES;
  const U = S.unit || { one: "precinct", many: "precincts", page: "The precinct's page on nyc.gov" };
  const D = await (await fetch(`data/${S.key}-coverage.json`)).json();
  const STATE = D.states;
  const fmt = n => n.toLocaleString("en-US");
  const dateLong = s => new Date(s + "T12:00:00").toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
  const W = D.weeks.length;
  const count = (p, s) => [...p.states].filter(c => c === String(s)).length;
  const t = D.totals;
  const held = t.read + t.older_layout + t.pdf_only;
  const ec = D.estimate_check;

  // Number tiles and other filled-in text
  const calc = {
    weeks: fmt(W),
    held: fmt(held),
    held_share: Math.round(100 * held / t.precinct_weeks) + "%",
    estimate: ec.files_with_both_dates ? `${fmt(ec.estimate_right)} of ${fmt(ec.files_with_both_dates)}` : "",
    span: W === 1 ? `week, ending ${dateLong(D.first_week)}` : `weeks from ${dateLong(D.first_week)} to ${dateLong(D.last_week)}`,
  };
  document.querySelectorAll("[data-total]").forEach(n => { n.textContent = fmt(t[n.dataset.total] ?? 0); });
  document.querySelectorAll("[data-calc]").forEach(n => { n.textContent = calc[n.dataset.calc]; });
  document.querySelectorAll("[data-if-estimate]").forEach(n => { n.hidden = !ec.files_with_both_dates; });
  const built = document.getElementById("built");
  if (built && D.last_file_saved) {
    built.dateTime = D.last_file_saved;
    built.textContent = new Date(D.last_file_saved).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "America/New_York" });
  }

  // Precinct picker, grouped by borough
  const pick = document.getElementById("pick");
  const groups = {};
  for (const p of D.precincts) (groups[p.borough] ||= []).push(p);
  for (const [b, ps] of Object.entries(groups)) {
    const og = document.createElement("optgroup"); og.label = b;
    for (const p of ps) og.append(new Option(p.name, p.id));
    pick.append(og);
  }

  // Table: the same facts as the chart, as text
  const tb = document.querySelector("#table tbody");
  for (const p of D.precincts) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<th scope="row"><a href="?precinct=${p.id}#detail-h" data-id="${p.id}">${p.name}</a></th><td>${p.borough}</td>` +
      [3, 2, 1, 0].map(s => `<td class="num">${count(p, s)}</td>`).join("");
    tb.append(tr);
  }
  tb.addEventListener("click", e => {
    const a = e.target.closest("a[data-id]");
    if (a) { e.preventDefault(); select(a.dataset.id); }
  });

  // Grid. A short series gets wider squares, so one week is still easy to see.
  const NS = "http://www.w3.org/2000/svg";
  const el = (n, a, parent) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); if (parent) parent.append(e); return e; };
  const CW = W > 150 ? 4 : W > 40 ? 6 : 14, CH = 10, GAP = 1, LABEL = 150, TOP = 34, BGAP = 18;
  let y = TOP; const rowY = {};
  for (const ps of Object.values(groups)) { y += BGAP; for (const p of ps) { rowY[p.id] = y; y += CH + GAP; } }
  const width = Math.max(LABEL + W * CW + 4, 260), height = y + 6;
  const svg = el("svg", { class: "cov", width, height, viewBox: `0 0 ${width} ${height}` });
  // Axis: a year label at each January (and at the start); a month initial each month
  // when the squares are wide enough to hold one
  let lastYear = "";
  D.weeks.forEach((w, i) => {
    const prev = i ? D.weeks[i - 1].slice(5, 7) : "";
    if (w.slice(5, 7) !== prev) {
      const x = LABEL + i * CW;
      const yr = w.slice(0, 4);
      if (yr !== lastYear) { el("text", { x, y: 11, class: "axis", "font-weight": 700 }, svg).textContent = yr; lastYear = yr; }
      if (CW >= 6 || W < 5) el("text", { x, y: 25, class: "axis" }, svg).textContent = new Date(w + "T12:00:00").toLocaleDateString("en-US", { month: "narrow" });
    }
  });
  for (const [b, ps] of Object.entries(groups)) {
    el("text", { x: 0, y: rowY[ps[0].id] - 5, class: "boro" }, svg).textContent = b;
    for (const p of ps) {
      const yy = rowY[p.id];
      const lab = el("text", { x: LABEL - 8, y: yy + CH - 1, "text-anchor": "end", class: "row-label", "data-id": p.id }, svg);
      lab.textContent = p.name.replace(" Precinct", "");
      [...p.states].forEach((s, i) => {
        el("rect", { class: `c s${s}`, x: LABEL + i * CW, y: yy, width: CW - GAP, height: CH, "data-id": p.id, "data-i": i }, svg);
      });
    }
  }
  const selRect = el("rect", { class: "sel", x: LABEL - 2, width: W * CW + 2, height: CH + 3, visibility: "hidden" }, svg);
  document.getElementById("chart").append(svg);
  document.getElementById("chart").setAttribute("aria-label",
    `Grid of ${D.precincts.length} ${D.precincts.length === 1 ? U.one : U.many} by ${W} week${W === 1 ? "" : "s"}, ${dateLong(D.first_week)} to ${dateLong(D.last_week)}. ` +
    `${fmt(t.read)} ${U.one}-weeks: ${STATE[3].toLowerCase()}; ${fmt(t.older_layout)}: spreadsheet in an older layout; ${fmt(t.pdf_only)}: PDF only; ${fmt(t.none)}: nothing saved. The table below has the same counts.`);

  // Tooltip
  const tip = document.getElementById("tip");
  const byId = Object.fromEntries(D.precincts.map(p => [p.id, p]));
  svg.addEventListener("mousemove", e => {
    const r = e.target.closest("rect.c");
    if (!r) { tip.hidden = true; return; }
    const p = byId[r.dataset.id], i = +r.dataset.i, s = p.states[i];
    tip.innerHTML = `<b>${p.name}</b><br>Week ending ${dateLong(D.weeks[i])}${p.estimated[i] === "1" ? " (estimated)" : ""}<br>${STATE[s]}`;
    tip.hidden = false;
    tip.style.left = Math.min(e.clientX + 14, innerWidth - 270) + "px";
    tip.style.top = (e.clientY + 14) + "px";
  });
  svg.addEventListener("mouseleave", () => { tip.hidden = true; });
  // A tap on a touch screen sends mousemove but never mouseleave, so the tooltip would
  // stay on screen; hide it on any click and whenever the page scrolls.
  addEventListener("scroll", () => { tip.hidden = true; }, { passive: true });
  svg.addEventListener("click", e => {
    tip.hidden = true;
    const target = e.target.closest("[data-id]");
    if (target) select(target.dataset.id);
  });

  // Detail for one precinct
  const detail = document.getElementById("detail");
  const detailHead = document.getElementById("detail-h");
  const empty = detail.innerHTML;  // the page's own "pick one" note
  const folders = S.folders.map(f => `<a href="${f.href}">${f.text}</a>`).join(", ");
  function render(view) {
    const p = byId[view.precinct];
    pick.value = p ? p.id : "";
    svg.querySelectorAll(".row-label.on").forEach(n => n.classList.remove("on"));
    if (!p) { detail.innerHTML = empty; selRect.setAttribute("visibility", "hidden"); return; }
    svg.querySelector(`.row-label[data-id="${p.id}"]`).classList.add("on");
    selRect.setAttribute("y", rowY[p.id] - 1.5); selRect.setAttribute("visibility", "visible");
    const n3 = count(p, 3), n2 = count(p, 2), n1 = count(p, 1), n0 = count(p, 0);
    const sw = Math.max(1, Math.min(24, Math.floor(Math.min(detail.parentElement.clientWidth - 40, 900) / W)));
    let strip = `<svg class="strip" width="${W * sw}" height="42" role="img" aria-label="${p.name}, one bar per week, ${dateLong(D.first_week)} to ${dateLong(D.last_week)}">`;
    [...p.states].forEach((s, i) => { strip += `<rect class="s${s}" x="${i * sw}" y="0" width="${Math.max(sw - 1, 1)}" height="26"><title>Week ending ${dateLong(D.weeks[i])}: ${STATE[s]}</title></rect>`; });
    // A year under the first week of each year, when there is room for it
    let lastX = -99;
    D.weeks.forEach((w, i) => {
      if ((i === 0 || w.slice(0, 4) !== D.weeks[i - 1].slice(0, 4)) && i * sw - lastX >= 34) {
        strip += `<text x="${i * sw}" y="40" class="axis">${w.slice(0, 4)}</text>`; lastX = i * sw;
      }
    });
    strip += "</svg>";
    const legend = document.querySelector(".legend").cloneNode(true);
    legend.removeAttribute("aria-label");
    legend.querySelectorAll("li").forEach((li, i) => { if (!count(p, 3 - i)) li.remove(); });  // items run 3, 2, 1, 0
    const span = W === 1 ? `the week ending ${dateLong(D.first_week)}` : `${fmt(W)} weeks from ${dateLong(D.first_week)} to ${dateLong(D.last_week)}`;
    detail.innerHTML = `<h3>${p.name}, ${p.borough}</h3>
      <p>Of ${span}, <b>${fmt(n3)}</b> ${n3 === 1 ? "has" : "have"} numbers in CSV${n2 ? `, ${fmt(n2)} more ${n2 === 1 ? "is a spreadsheet" : "are spreadsheets"} we saved but have not read yet` : ""}${n1 ? `, and ${fmt(n1)} ${n1 === 1 ? "is" : "are"} PDF only` : ""}. <b>${fmt(n0)}</b> ${n0 === 1 ? "week is" : "weeks are"} missing.</p>
      <div class="strip-wrap">${strip}</div>
      ${legend.outerHTML}
      <ul>
        <li>The numbers: ${folders}</li>
        ${p.nypd_page ? `<li><a href="${p.nypd_page}">${U.page}</a>, which links this week's file</li>` : ""}
        ${p.nypd_file ? `<li><a href="${p.nypd_file}">${S.nypdFile}</a>, from NYPD</li>` : ""}
        <li><a href="#coverage">Back to the chart</a></li>
      </ul>`;
  }
  // A choice made on the page moves to its section, so the result is never off screen.
  function show() {
    detailHead.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    detailHead.focus({ preventScroll: true });
  }
  function select(id) { Share.write({ precinct: id }); render(Share.read()); if (id) show(); }
  pick.addEventListener("change", () => select(pick.value));

  // A report with one file for the whole city has one row: show it from the start.
  const start = Share.read({ precinct: D.precincts.length === 1 ? D.precincts[0].id : "" });
  render(start);
  Share.onChange(render);
  Share.copyButtons();
  if (location.hash === "#coverage") document.getElementById("coverage").scrollIntoView();
  else if (location.hash === "#detail-h" && byId[start.precinct]) show();
});
