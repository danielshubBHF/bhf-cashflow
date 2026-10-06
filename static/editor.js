// Row editing: mark changed rows, save with fetch (so a refused save keeps what was typed),
// then reload so every figure is re-run. Without JS the forms still post and redirect.
// A "row" is the form's container: a milestone <tr>, or the line edit form itself (.edit-row).
(() => {
  const SAVED = "bhf-saved";
  const store = (k, v) => { try { v === undefined ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch (e) {} };
  const read = k => { try { return sessionStorage.getItem(k); } catch (e) { return null; } };

  if (read(SAVED)) {
    const t = document.createElement("div");
    t.className = "toast"; t.setAttribute("role", "status"); t.textContent = "demo" in document.body.dataset ? "Saved (demo only, not written to Smartsheet)" : "Saved to Smartsheet";
    document.body.append(t); setTimeout(() => t.remove(), 2500);
    store(SAVED);
  }

  let saving = false;
  const num = s => parseFloat(String(s || "").replace(/[$,\s]/g, ""));
  const money = n => n.toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const dirty = () => document.querySelector(".dirty");

  // Remember what's open so dash.js can put it back after the reload.
  function remember() {
    const keys = [...document.querySelectorAll(".fl.open[data-key], details[data-key][open]")].map(d => d.dataset.key)
      .filter(k => !k.startsWith("new-"));                  // a new line is saved: its blank form stays shut
    store("bhf-open", JSON.stringify(keys));
    const drawer = document.querySelector(".ovl.on");
    if (drawer && drawer.classList.contains("side") && !drawer.querySelector(".lp-edit")?.hidden) store("bhf-edit", "1");
    if (drawer) { store("bhf-drawer", drawer.id);store("bhf-drawer-scroll", String(drawer.querySelector(".sheet-b")?.scrollTop || 0)); }
  }

  document.querySelectorAll("form.rowform").forEach(form => {
    const row = form.closest("tr, .edit-row"), fid = form.getAttribute("id");   // form.id would be the hidden "id" input
    const fields = [...form.elements].filter(el => el.name && el.type !== "hidden");
    const msg = row.querySelector(".msg");
    const base = () => num(row.querySelector("select.po")?.selectedOptions[0]?.dataset.base ?? row.dataset.base);
    const field = cls => row.querySelector(`[form="${fid}"].${cls}`) || form.querySelector(`.${cls}`);

    row.addEventListener("input", e => {
      if (!fields.includes(e.target)) return;
      row.classList.add("dirty"); msg.textContent = ""; msg.className = "msg";
      const b = base(), pct = field("pct"), amt = field("amt");
      if (b > 0 && pct && e.target === pct && !isNaN(num(pct.value))) amt.value = money(b * num(pct.value) / 100);
      if (b > 0 && amt && e.target === amt && !isNaN(num(amt.value))) pct.value = +(num(amt.value) / b * 100).toFixed(4);
    });

    // Cancel (line forms): back to what was loaded, and fold the line away.
    row.querySelector("[data-cancel]")?.addEventListener("click", () => {
      form.reset(); row.classList.remove("dirty"); msg.textContent = ""; msg.className = "msg";
      const fl = row.closest(".fl");
      if (fl && window.bhfDrawer) { window.bhfDrawer.expand(fl, false); fl.querySelector(".fl-h, .fl-add")?.focus(); }
    });

    form.addEventListener("submit", async e => {
      e.preventDefault();
      const by = e.submitter;
      if (saving || (by?.name === "delete" && !confirm("Remove this milestone?"))) return;
      saving = true; msg.textContent = "Saving…"; msg.className = "msg";
      try {
        const r = await fetch(form.action, { method: "POST", body: new FormData(form, by), headers: { "X-Fetch": "1" } });
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || "Not saved");
        row.classList.remove("dirty");
        if (dirty()) {                                   // other rows still being edited: don't lose them
          saving = false; msg.textContent = "Saved. Figures update when the other rows are saved."; msg.className = "msg ok";
          return;
        }
        remember();
        store(SAVED, "1");
        location.reload();
      } catch (err) {
        saving = false; msg.textContent = err.message; msg.className = "msg err";
      }
    });
  });

  window.addEventListener("beforeunload", e => {
    if (!saving && dirty()) { e.preventDefault(); e.returnValue = ""; }
  });
})();
