// Drawers (.ovl), the line detail side panels (.ovl.side), expandable lines in Forecast & payments (.fl)
// and the In / Out list filters.
//  - [data-open="<id>"] opens a drawer or panel; with data-line="<key>" it also expands that line.
//  - [data-edit="<key>"] in a line panel switches it to edit mode: that line's form and milestone editor are
//    moved in from the Forecast & payments drawer (so there is only ever one copy of each form), and moved
//    back when the panel closes. ✕, Esc or a click on the backdrop closes.
//  - After a save editor.js reloads the page and leaves the open drawer / panel, edit mode, open lines and
//    scroll here, so everything comes back as it was.
// Without JS the drawers and panels are sections at the bottom of the page and lines are open.
(() => {
  const get = k => { try { return sessionStorage.getItem(k); } catch (e) { return null; } };
  const del = k => { try { sessionStorage.removeItem(k); } catch (e) {} };
  const lget = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const lset = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
  let back = null;

  // ---- lines in Forecast & payments (accordion; several may be open)
  const lineEl = key => document.querySelector(`.fl[data-key="${CSS.escape(String(key))}"]`);
  function expand(fl, on = true) {
    if (!fl) return;
    fl.classList.toggle("open", on);
    fl.querySelector(":scope > .fl-h, :scope > .fl-add")?.setAttribute("aria-expanded", String(on));
  }
  function reveal(key) {
    const fl = lineEl(key);
    if (!fl) return;
    expand(fl);
    fl.scrollIntoView({ block: "start" });
    fl.querySelector(".fl-h, .fl-add")?.focus({ preventScroll: true });
  }

  // ---- line panel edit mode: borrow the line's editor from the drawer
  const moved = [];                       // [node, placeholder]
  function editMode(panel, on) {
    if (!panel) return;
    const key = panel.dataset.key, read = panel.querySelector(".lp-read"), edit = panel.querySelector(".lp-edit");
    if (!edit) return;
    if (on) {
      const src = document.getElementById(`fl-${key}-p`);
      const slot = edit.querySelector(".lp-slot");
      if (src && !slot.children.length) {
        src.querySelectorAll(":scope > .lform, :scope > .fl-cols > .fl-sched").forEach(n => {
          const ph = document.createComment("lp"); n.before(ph); moved.push([n, ph]); slot.append(n);
        });
      }
      read.hidden = true; edit.hidden = false;
      panel.querySelector(".sheet")?.classList.add("wide-edit");
      edit.querySelector("input, select")?.focus();
    } else {
      putBack();
      read.hidden = false; edit.hidden = true;
      panel.querySelector(".sheet")?.classList.remove("wide-edit");
    }
  }
  function putBack() { while (moved.length) { const [n, ph] = moved.pop(); ph.replaceWith(n); } }

  // ---- drawers and panels
  const current = () => document.querySelector(".ovl.on");
  function close() {
    const d = current();
    if (!d) return;
    if (d.classList.contains("side")) editMode(d, false);
    d.classList.remove("on");
    document.body.classList.remove("locked");
    if (back && back.focus) back.focus({ preventScroll: true });
  }
  function open(id, scroll) {
    const d = document.getElementById(id);
    if (!d || !d.classList.contains("ovl")) return false;
    if (current() !== d) { close(); back = document.activeElement; }
    d.classList.add("on");
    document.body.classList.add("locked");
    const b = d.querySelector(".sheet-b");
    if (b && scroll != null) b.scrollTop = +scroll;
    (d.querySelector(".x") || d).focus({ preventScroll: true });
    return true;
  }
  window.bhfDrawer = { open, close, current, expand, reveal, editMode };

  document.addEventListener("click", e => {
    const h = e.target.closest(".fl-h, .fl-add");
    if (h) { const fl = h.closest(".fl"); expand(fl, !fl.classList.contains("open")); return; }
    const ed = e.target.closest("[data-edit]");
    if (ed) { editMode(ed.closest(".ovl.side"), true); return; }
    const vw = e.target.closest("[data-view]");
    if (vw) { editMode(vw.closest(".ovl.side"), false); return; }
    const cancel = e.target.closest(".lp-slot [data-cancel]");
    if (cancel) { setTimeout(() => editMode(cancel.closest(".ovl.side"), false)); return; }
    const o = e.target.closest("[data-open]");
    if (o) {
      if (open(o.dataset.open)) { e.preventDefault(); if (o.dataset.line) reveal(o.dataset.line); }
      return;
    }
    if (e.target.closest("[data-close]") || e.target.classList.contains("ovl")) close();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && current()) { close(); return; }
    const o = e.target.closest && e.target.closest('[data-open][role="button"]');
    if (o && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); open(o.dataset.open); }
  });

  // ---- In / Out list filters: All · To come · Done · Overdue (remembered per list)
  document.querySelectorAll(".llist[data-list]").forEach(list => {
    const KEY = `bhf-filter-${list.dataset.list}`;
    function apply(f) {
      list.querySelectorAll(".fchips button").forEach(b => b.classList.toggle("on", b.dataset.filter === f));
      let any = false;
      list.querySelectorAll(".lg-line").forEach(g => {
        let n = 0;
        g.querySelectorAll(".pr[data-state]").forEach(r => {
          const st = r.dataset.state.split(" ");
          const show = f === "all" || st.includes(f);
          r.hidden = !show; n += show ? 1 : 0;
        });
        g.hidden = f !== "all" && n === 0;
        any = any || !g.hidden;
      });
      const none = list.querySelector(".ll-none"); if (none) none.hidden = any;
    }
    list.querySelectorAll(".fchips button").forEach(b => b.addEventListener("click", () => { lset(KEY, b.dataset.filter); apply(b.dataset.filter); }));
    const f = lget(KEY);
    if (f && f !== "all" && list.querySelector(`.fchips [data-filter="${f}"]`)) apply(f);
  });

  // ---- after a save reload: the same drawer / panel, edit mode, lines and scroll; else follow #forecast
  const saved = get("bhf-drawer"), lines = JSON.parse(get("bhf-open") || "[]"), editing = get("bhf-edit");
  lines.forEach(k => expand(lineEl(k)));
  document.querySelectorAll("details[data-key]").forEach(d => { if (lines.includes(d.dataset.key)) d.open = true; });
  if (saved) {
    const d = document.getElementById(saved);
    if (d && editing && d.classList.contains("side")) editMode(d, true);
    open(saved, get("bhf-drawer-scroll"));
  } else if (location.hash.length > 1) {
    const el = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    const d = el && el.closest(".ovl");
    if (d) open(d.id);
  }
  ["bhf-drawer", "bhf-drawer-scroll", "bhf-open", "bhf-edit"].forEach(del);
})();

// Outgoing table: ▸ shows / hides a line's payment stages.
document.addEventListener("click", e => {
  const b = e.target.closest(".ct .tg");
  if (!b) return;
  const tr = b.closest("tr"), open = b.getAttribute("aria-expanded") !== "true";
  b.setAttribute("aria-expanded", String(open));
  document.querySelectorAll(`.ct tr.stg[data-for="${CSS.escape(tr.dataset.k)}"]`).forEach(r => { r.hidden = !open; });
});

// Forms that change something important ask first (e.g. locking the P&L baseline).
document.addEventListener("submit", e => {
  const m = e.target.dataset && e.target.dataset.confirm;
  if (m && !confirm(m)) e.preventDefault();
});
