/* Shareable views for BetaNYC Pages sites.

   A view (which precinct, which year, which measure) lives in the page address as
   query parameters, so any view can be copied, bookmarked and posted:

     https://betanyc.github.io/example/?precinct=034#coverage

   Usage:
     const view = Share.read({ precinct: "" });        // defaults for anything missing
     Share.write({ precinct: "034" });                 // update the address, no reload
     Share.onChange(view => render(view));             // back/forward buttons
     Share.copyButtons();                              // wire every .copy-link button

   A .copy-link button copies the current address, with its #anchor set to the
   data-anchor attribute (usually the id of the figure it sits in).

   Social sites read a page's Open Graph tags, not its query string, so every shared
   view gets the same preview card. Put the view in the post text. */
(function () {
  const defaults = {};

  function read(defs) {
    Object.assign(defaults, defs || {});
    const q = new URLSearchParams(location.search);
    const view = {};
    for (const k of Object.keys(defaults)) view[k] = q.has(k) ? q.get(k) : defaults[k];
    return view;
  }

  function write(changes) {
    const q = new URLSearchParams(location.search);
    for (const [k, v] of Object.entries(changes)) {
      if (v === "" || v == null || v === defaults[k]) q.delete(k);
      else q.set(k, v);
    }
    const s = q.toString();
    history.replaceState(null, "", location.pathname + (s ? "?" + s : "") + location.hash);
  }

  function onChange(fn) {
    // replaceState fires nothing, so this only runs for back/forward and for a link to
    // another view of the same page.
    addEventListener("popstate", () => fn(read()));
  }

  function linkFor(anchor) {
    const u = new URL(location.href);
    if (anchor) u.hash = anchor;
    return u.toString();
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (e) {
      const t = document.createElement("textarea");
      t.value = text; t.setAttribute("readonly", ""); t.style.position = "absolute"; t.style.left = "-9999px";
      document.body.appendChild(t); t.select();
      let ok = false; try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
      t.remove(); return ok;
    }
  }

  function copyButtons(root) {
    (root || document).querySelectorAll(".copy-link").forEach(btn => {
      if (btn.dataset.wired) return;
      btn.dataset.wired = "1";
      const label = btn.textContent;
      btn.addEventListener("click", async () => {
        const ok = await copy(linkFor(btn.dataset.anchor));
        btn.textContent = ok ? "Link copied" : "Copy failed";
        if (ok) btn.setAttribute("data-copied", "");
        setTimeout(() => { btn.textContent = label; btn.removeAttribute("data-copied"); }, 2000);
      });
    });
  }

  window.Share = { read, write, onChange, linkFor, copyButtons };
})();
