/* App shell: modal system, toasts, company picker (auto-activate on select),
   pairing modal, connector status dot, download action, shared helpers and
   the searchable dropdown used by the voucher grid. */
(function () {
  "use strict";

  // ---- shared helpers --------------------------------------------------------
  window.escapeHtml = function (text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  };

  const inr = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  window.ABSFormat = {
    money: (v) => (v == null || v === "" || isNaN(v) ? "" : inr.format(Number(v))),
    /* YYYYMMDD -> DD-MM-YYYY for display */
    date: function (raw) {
      raw = (raw || "").trim();
      if (/^\d{8}$/.test(raw)) return raw.slice(6, 8) + "-" + raw.slice(4, 6) + "-" + raw.slice(0, 4);
      return raw;
    },
    /* Accepts DD-MM-YYYY, DD/MM/YYYY or YYYYMMDD; returns YYYYMMDD or the input */
    dateRaw: function (text) {
      text = (text || "").trim();
      if (/^\d{8}$/.test(text)) return text;
      const m = text.match(/^(\d{1,2})[-\/](\d{1,2})[-\/](\d{4})$/);
      if (m) return m[3] + m[2].padStart(2, "0") + m[1].padStart(2, "0");
      return text;
    },
  };

  /* Wire a Show/Hide button to reveal a password input's text. */
  window.ABSPwToggle = function (btn, input) {
    if (!btn || !input) return;
    btn.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.textContent = show ? "Hide" : "Show";
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    });
  };

  // ---- toasts (stacked) --------------------------------------------------------
  window.ABSToast = function (message, isError, isSuccess) {
    const stack = document.getElementById("toast-stack");
    if (!stack) return;
    const el = document.createElement("div");
    el.className = "toast" + (isError ? " error" : "") + (isSuccess ? " success" : "");
    el.textContent = message;
    stack.appendChild(el);
    let timer = setTimeout(remove, isError ? 6500 : 3500);
    el.addEventListener("mouseenter", () => clearTimeout(timer));
    el.addEventListener("mouseleave", () => { timer = setTimeout(remove, 1500); });
    function remove() { el.remove(); }
    while (stack.children.length > 4) stack.firstChild.remove();
  };

  // ---- modal system ---------------------------------------------------------------
  const ABSModal = {
    _stack: [],

    open(opts) {
      // opts: { title, bodyHtml, footHtml, large, onClose }
      const overlay = document.createElement("div");
      overlay.className = "modal-overlay";
      overlay.innerHTML =
        '<div class="modal' + (opts.large ? " modal-lg" : "") + '" role="dialog" aria-modal="true">' +
        '<div class="modal-head"><h2>' + escapeHtml(opts.title || "") + "</h2>" +
        '<button class="modal-x" aria-label="Close">&times;</button></div>' +
        '<div class="modal-body">' + (opts.bodyHtml || "") + "</div>" +
        (opts.footHtml ? '<div class="modal-foot">' + opts.footHtml + "</div>" : "") +
        "</div>";
      document.getElementById("modal-root").appendChild(overlay);

      const prevFocus = document.activeElement;
      const handle = {
        el: overlay,
        body: overlay.querySelector(".modal-body"),
        foot: overlay.querySelector(".modal-foot"),
        close() {
          const i = ABSModal._stack.indexOf(handle);
          if (i >= 0) ABSModal._stack.splice(i, 1);
          overlay.remove();
          if (prevFocus && prevFocus.focus) prevFocus.focus();
          if (opts.onClose) opts.onClose();
        },
      };
      ABSModal._stack.push(handle);

      overlay.querySelector(".modal-x").addEventListener("click", handle.close);
      overlay.addEventListener("mousedown", (e) => {
        if (e.target === overlay && !opts.blockOverlayClose) handle.close();
      });
      // focus first control
      setTimeout(() => {
        const first = overlay.querySelector("input, select, textarea, button:not(.modal-x)");
        if (first) first.focus();
      }, 30);
      return handle;
    },

    /* styled confirm; returns Promise<boolean> */
    confirm(opts) {
      return new Promise((resolve) => {
        const h = ABSModal.open({
          title: opts.title || "Are you sure?",
          bodyHtml: '<div>' + (opts.bodyHtml || escapeHtml(opts.body || "")) + "</div>",
          footHtml:
            '<button class="btn" data-act="cancel">' + escapeHtml(opts.cancelText || "Cancel") + "</button>" +
            '<button class="btn ' + (opts.danger ? "btn-danger" : "btn-primary") + '" data-act="ok">' +
            escapeHtml(opts.okText || "Confirm") + "</button>",
          onClose: () => resolve(false),
        });
        h.foot.querySelector('[data-act="cancel"]').addEventListener("click", () => h.close());
        h.foot.querySelector('[data-act="ok"]').addEventListener("click", () => {
          resolve(true);
          h.el.remove();
          const i = ABSModal._stack.indexOf(h);
          if (i >= 0) ABSModal._stack.splice(i, 1);
        });
        // avoid destructive default focus
        if (opts.danger) h.foot.querySelector('[data-act="cancel"]').focus();
      });
    },
  };
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && ABSModal._stack.length) {
      ABSModal._stack[ABSModal._stack.length - 1].close();
    }
  });
  window.ABSModal = ABSModal;

  // ---- searchable dropdown ----------------------------------------------------------
  /* ABSDropdown(cfg) -> element.
     cfg: { placeholder, value: {mode, name} | null, best: name|null,
            options: [names], onPick(name), onCreate() }
     Renders a button; clicking opens a search panel with the fuzzy best match
     first, all other masters below and "Create new..." at the bottom. */
  window.ABSDropdown = function (cfg) {
    const root = document.createElement("div");
    root.className = "sdrop";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "sdrop-btn";
    root.appendChild(btn);

    let panel = null;
    let reposition = null;

    function labelHtml() {
      const v = cfg.value;
      if (v && v.mode === "existing" && v.name) {
        return '<span class="val chosen">' + escapeHtml(v.name) + "</span>";
      }
      if (v && v.mode === "new" && v.name) {
        return '<span class="val newmark">New: ' + escapeHtml(v.name) + "</span>";
      }
      return '<span class="val">' + escapeHtml(cfg.placeholder || "Select Ledger") + "</span>";
    }
    function renderBtn() {
      btn.innerHTML = labelHtml() +
        '<svg viewBox="0 0 24 24"><path d="M7 10l5 5 5-5H7z"/></svg>';
    }
    renderBtn();

    function closePanel() {
      if (panel) { panel.remove(); panel = null; document.removeEventListener("mousedown", outside); }
      if (reposition) {
        window.removeEventListener("scroll", reposition, true);
        window.removeEventListener("resize", reposition);
        reposition = null;
      }
    }
    // Panel is portaled to <body>, so a click inside it is not inside root -
    // treat clicks in either as "inside" the control.
    function outside(e) {
      if (panel && !root.contains(e.target) && !panel.contains(e.target)) closePanel();
    }

    function openPanel() {
      if (panel) { closePanel(); return; }
      panel = document.createElement("div");
      panel.className = "sdrop-panel";
      panel.innerHTML =
        '<input class="sdrop-search" placeholder="Search...">' +
        '<div class="sdrop-list"></div>' +
        '<div class="sdrop-create">+ Create new...</div>';
      // Portal to body with fixed positioning so no scroll/overflow ancestor
      // (the horizontally scrolling voucher line) can clip it. This is what
      // stops the vertical scrollbar and shows the full list.
      document.body.appendChild(panel);
      const search = panel.querySelector(".sdrop-search");
      const list = panel.querySelector(".sdrop-list");

      let hl = -1;
      let visible = [];

      function positionPanel() {
        if (!panel) return;
        const r = btn.getBoundingClientRect();
        panel.style.position = "fixed";
        panel.style.minWidth = Math.max(r.width, 240) + "px";
        const ph = panel.offsetHeight;
        const pw = panel.offsetWidth;
        const spaceBelow = window.innerHeight - r.bottom;
        // Open downward normally; flip up if there is more room above.
        const top = (spaceBelow < ph + 8 && r.top > spaceBelow)
          ? Math.max(8, r.top - ph - 4)
          : r.bottom + 4;
        let left = r.left;
        if (left + pw > window.innerWidth - 8) left = Math.max(8, window.innerWidth - pw - 8);
        panel.style.top = top + "px";
        panel.style.left = left + "px";
      }

      function draw(filter) {
        const f = (filter || "").toUpperCase();
        const opts = cfg.options || [];
        const best = cfg.best && opts.indexOf(cfg.best) >= 0 ? cfg.best : null;
        const ordered = best ? [best].concat(opts.filter((o) => o !== best)) : opts.slice();
        visible = ordered.filter((o) => !f || o.toUpperCase().indexOf(f) >= 0);
        hl = visible.length ? 0 : -1;
        list.innerHTML = "";
        if (!visible.length) {
          list.innerHTML = '<div class="sdrop-empty">' +
            ((cfg.options || []).length ? "No match." :
              "Nothing synced from Tally yet. Use Create new, or sync the connector.") +
            "</div>";
          return;
        }
        visible.forEach((name, i) => {
          const item = document.createElement("div");
          const isBest = name === best;
          const isSel = cfg.value && cfg.value.mode === "existing" && cfg.value.name === name;
          item.className = "sdrop-item" + (isBest ? " best" : "") + (isSel ? " selected" : "") + (i === hl ? " hl" : "");
          item.innerHTML = "<span>" + escapeHtml(name) + "</span>" +
            (isBest ? '<span class="tag">best match</span>' : '<span class="tag">from Tally</span>');
          item.addEventListener("click", () => pick(name));
          list.appendChild(item);
        });
      }

      function pick(name) {
        cfg.value = { mode: "existing", name: name };
        renderBtn();
        closePanel();
        cfg.onPick(name);
      }

      search.addEventListener("input", () => { draw(search.value); positionPanel(); });
      search.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown") { hl = Math.min(hl + 1, visible.length - 1); draw(search.value ? search.value : ""); highlight(); e.preventDefault(); }
        else if (e.key === "ArrowUp") { hl = Math.max(hl - 1, 0); highlight(); e.preventDefault(); }
        else if (e.key === "Enter") { if (hl >= 0 && visible[hl]) pick(visible[hl]); e.preventDefault(); }
        else if (e.key === "Escape") { closePanel(); e.stopPropagation(); }
      });
      function highlight() {
        list.querySelectorAll(".sdrop-item").forEach((el, i) =>
          el.classList.toggle("hl", i === hl));
      }

      panel.querySelector(".sdrop-create").addEventListener("click", () => {
        closePanel();
        cfg.onCreate();
      });

      draw("");
      positionPanel();
      reposition = positionPanel;
      window.addEventListener("scroll", reposition, true);
      window.addEventListener("resize", reposition);
      document.addEventListener("mousedown", outside);
      setTimeout(() => search.focus(), 10);
    }

    btn.addEventListener("click", openPanel);
    root._setValue = function (v) { cfg.value = v; renderBtn(); };
    root._setOptions = function (opts, best) { cfg.options = opts; cfg.best = best; };
    return root;
  };

  // ---- company picker -----------------------------------------------------------------
  const COMPANY_KEY = "abs_company_id";
  let companies = [];

  const ABSShell = {
    companyId: () => localStorage.getItem(COMPANY_KEY) || "",
    company: () => companies.find((c) => c.id === ABSShell.companyId()) || null,
    companies: () => companies,

    async refreshCompanies() {
      if (!window.ABSAuth.isLoggedIn()) return;
      try {
        companies = await window.ABSApi.get("/companies");
      } catch (ex) { companies = []; }
      // Drop a stored selection that no longer exists.
      if (ABSShell.companyId() && !ABSShell.company()) localStorage.removeItem(COMPANY_KEY);
      renderCompanyButton();
    },

    async selectCompany(id) {
      const c = companies.find((x) => x.id === id);
      if (!c) return;
      localStorage.setItem(COMPANY_KEY, id);
      renderCompanyButton();
      try {
        const updated = await window.ABSApi.put("/companies/" + id + "/select");
        const i = companies.findIndex((x) => x.id === id);
        if (i >= 0) companies[i] = updated;
        window.ABSToast("Company selected: " + c.tally_name + ". Masters are refreshing.", false, true);
      } catch (ex) {
        window.ABSToast(ex.message, true);
      }
      document.dispatchEvent(new CustomEvent("abs:company-changed", { detail: { id } }));
    },

    async deleteCompany(id) {
      const c = companies.find((x) => x.id === id);
      if (!c) return;
      await window.ABSApi.del("/companies/" + id);
      const wasSelected = ABSShell.companyId() === id;
      if (wasSelected) localStorage.removeItem(COMPANY_KEY);
      await ABSShell.refreshCompanies();
      // Reload the current view's data if the deleted company was selected, so
      // its dashboard/vouchers/history stop showing.
      if (wasSelected) {
        document.dispatchEvent(new CustomEvent("abs:company-changed",
          { detail: { id: ABSShell.companyId() } }));
      }
    },
  };
  window.ABSShell = ABSShell;

  function renderCompanyButton() {
    const label = document.getElementById("company-label");
    if (!label) return;
    const c = ABSShell.company();
    label.textContent = c ? c.tally_name : "Select company";
  }

  function renderCompanyMenu() {
    const menu = document.getElementById("company-menu");
    menu.innerHTML = "";
    if (!companies.length) {
      menu.innerHTML = '<div class="menu-note">No companies synced yet.<br>' +
        "Pair the desktop connector (sidebar), open your company in Tally and press Sync in the connector window.</div>";
      return;
    }
    companies.forEach((c) => {
      const item = document.createElement("div");
      item.className = "company-item" + (c.id === ABSShell.companyId() ? " selected" : "");
      item.innerHTML = '<span class="name">' + escapeHtml(c.tally_name) + "</span>" +
        (c.synced_by_name
          ? '<span class="company-owner" title="Synced by">' + escapeHtml(c.synced_by_name) + "</span>"
          : "") +
        '<span class="company-item-actions">' +
        '<button class="edu-toggle' + (c.education_mode ? " on" : "") + '" ' +
        'title="Education mode: coerce voucher dates for educational Tally">' +
        "Edu " + (c.education_mode ? "on" : "off") + "</button>" +
        '<button class="company-del icon-btn danger" title="Delete this company and all its records">' +
        '<svg viewBox="0 0 24 24"><path d="M6 7h12l-1 13H7L6 7zm3-3h6l1 2H8l1-2zM4 6h16v2H4V6z"/></svg>' +
        "</button></span>";
      item.addEventListener("click", (e) => {
        if (e.target.closest(".edu-toggle") || e.target.closest(".company-del")) return;
        document.getElementById("company-menu").hidden = true;
        ABSShell.selectCompany(c.id);
      });
      item.querySelector(".company-del").addEventListener("click", async (e) => {
        e.stopPropagation();
        const ok = await ABSModal.confirm({
          title: "Delete company?",
          bodyHtml: "Permanently delete <strong>" + escapeHtml(c.tally_name) +
            "</strong> and everything in this app derived from it - its dashboard, " +
            "vouchers and history. This does <strong>not</strong> touch Tally, and does " +
            "not affect any other user's companies. This cannot be undone.",
          okText: "Delete company",
          cancelText: "Cancel",
          danger: true,
        });
        if (!ok) return;
        try {
          await ABSShell.deleteCompany(c.id);
          renderCompanyMenu();
          window.ABSToast("Company deleted: " + c.tally_name + ".");
        } catch (ex) { window.ABSToast(ex.message, true); }
      });
      item.querySelector(".edu-toggle").addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          const updated = await window.ABSApi.put("/companies/" + c.id + "/education-mode",
            { education_mode: !c.education_mode });
          const i = companies.findIndex((x) => x.id === c.id);
          if (i >= 0) companies[i] = updated;
          renderCompanyMenu();
          window.ABSToast("Education mode " + (updated.education_mode ? "on" : "off") +
            " for " + c.tally_name + ".");
        } catch (ex) { window.ABSToast(ex.message, true); }
      });
      menu.appendChild(item);
    });
  }

  // ---- connector status dot --------------------------------------------------------------
  let statusTimer = null;
  async function pollConnectorStatus() {
    if (!window.ABSAuth.isLoggedIn()) return;
    try {
      const s = await window.ABSApi.get("/connector/status");
      const dot = document.getElementById("conn-dot");
      const text = document.getElementById("conn-text");
      dot.className = "conn-dot " + (s.online ? "online" : "offline");
      if (s.online) {
        text.textContent = "Connector online";
      } else if (s.total === 0) {
        text.textContent = "No connector paired";
      } else {
        text.textContent = "Connector offline";
      }
    } catch (ex) { /* leave last state */ }
  }
  function startStatusPolling() {
    if (statusTimer) clearInterval(statusTimer);
    statusTimer = setInterval(pollConnectorStatus, 15000);
    pollConnectorStatus();
  }

  // ---- pairing modal ------------------------------------------------------------------------
  /* Two exclusive states, never both on screen at once:

       idle  - intro + name field + the "Previously generated" list, footer
               [Close] [Generate code].
       code  - one freshly issued code and nothing else; the name field, intro
               and the whole history list are hidden, footer [Generate another]
               [Done].

     Showing a live code next to a red "Generate code" button (and a "New code"
     button on every history row) read as an instruction to generate again, so
     users made a second connector for the same machine. Both ways of issuing a
     code - the footer button and a row's "New code" - render through showCode()
     alone, which makes two codes on screen structurally impossible. */
  function openPairingModal() {
    const h = ABSModal.open({
      title: "Pair a desktop connector",
      bodyHtml:
        '<div id="pair-form">' +
        '<p class="muted" style="margin-top:0">Name the computer that runs Tally, generate a one-time code, ' +
        "then enter the code in the desktop connector on that computer.</p>" +
        '<div class="field"><label>Connector name</label>' +
        '<input id="pair-name" placeholder="e.g. Office PC"></div>' +
        '<div class="pair-history">' +
        '<div class="pair-history-head">Previously generated</div>' +
        '<div id="pair-list" class="pair-list">' +
        '<div class="muted" style="font-size:13px">Loading...</div></div></div></div>' +
        '<div id="pair-result" hidden></div>',
      footHtml:
        '<button class="btn" data-act="close">Close</button>' +
        '<button class="btn btn-primary" data-act="gen">Generate code</button>',
    });
    const form = h.body.querySelector("#pair-form");
    const result = h.body.querySelector("#pair-result");
    const closeBtn = h.foot.querySelector('[data-act="close"]');
    const genBtn = h.foot.querySelector('[data-act="gen"]');

    closeBtn.addEventListener("click", h.close);
    loadPairHistory(h);

    /* Swap to the code state. `note` explains what this particular code is for,
       since a brand-new connector and a re-paired one need different wording. */
    function showCode(code, note) {
      form.hidden = true;
      result.hidden = false;
      result.innerHTML =
        '<div class="pair-history-head">Your pairing code</div>' +
        '<div class="code-box">' + escapeHtml(code) + "</div>" +
        '<p class="muted" style="font-size:13px">' + note + "</p>" +
        '<button class="btn btn-sm" data-act="copy">Copy code</button> ' +
        '<button class="btn btn-sm" data-act="dl">Download connector</button>';
      result.querySelector('[data-act="copy"]').addEventListener("click", () => {
        navigator.clipboard.writeText(code).then(
          () => window.ABSToast("Code copied."),
          () => window.ABSToast("Copy failed - select the code manually.", true));
      });
      result.querySelector('[data-act="dl"]').addEventListener("click", downloadConnector);
      // The escape hatch is a plain secondary button: reachable, but quieter
      // than the red primary so it does not invite a second code.
      closeBtn.textContent = "Done";
      genBtn.textContent = "Generate another";
      genBtn.classList.remove("btn-primary");
    }

    function resetToIdle() {
      result.hidden = true;
      result.innerHTML = "";
      form.hidden = false;
      h.body.querySelector("#pair-name").value = "";
      closeBtn.textContent = "Close";
      genBtn.textContent = "Generate code";
      genBtn.classList.add("btn-primary");
      loadPairHistory(h);  // pick up the code_pending flag we just set
    }
    h._showCode = showCode;  // used by a history row's "New code"

    genBtn.addEventListener("click", async () => {
      // In the code state this button reads "Generate another": it only takes
      // us back to the form, so a second code is always a deliberate click.
      if (!result.hidden) { resetToIdle(); return; }
      const name = h.body.querySelector("#pair-name").value.trim();
      if (!name) { window.ABSToast("Enter a connector name first.", true); return; }
      genBtn.disabled = true;
      try {
        const res = await window.ABSApi.post("/connector/pairing-code", { name });
        showCode(res.pairing_code,
          "This code is shown only once and works one time. Open the connector " +
          "on the Tally computer and paste it there.");
      } catch (ex) {
        window.ABSToast(ex.message, true);
      } finally {
        genBtn.disabled = false;
      }
    });
  }

  /* Load the caller's own connectors into the pairing modal's history list.
     Only the name, live status and whether a code is still unused are shown -
     never the code itself (the server never returns it). Each row can be
     deleted (blocked server-side if the connector has synced companies). */
  async function loadPairHistory(h) {
    const list = h.body.querySelector("#pair-list");
    if (!list) return;
    let items;
    try {
      items = await window.ABSApi.get("/connector/list");
    } catch (ex) {
      list.innerHTML = '<div class="muted" style="font-size:13px">Could not load the list.</div>';
      return;
    }
    if (!items.length) {
      list.innerHTML = '<div class="muted" style="font-size:13px">No connectors generated yet.</div>';
      return;
    }
    list.innerHTML = "";
    items.forEach((c) => {
      const row = document.createElement("div");
      row.className = "pair-row";
      row.innerHTML =
        '<span class="conn-dot ' + (c.online ? "online" : "offline") + '" ' +
        'title="' + (c.online ? "Online" : "Offline") + '"></span>' +
        '<span class="pair-row-name">' + escapeHtml(c.name) + "</span>" +
        (c.code_pending
          // Codes never expire, so one issued days ago is still valid and waiting.
          // Saying so is what stops the user generating a duplicate connector for
          // a machine that already has a usable code out there.
          ? '<button class="btn btn-sm pair-recode pair-pending" title="A code for this ' +
            'connector was generated and has not been used yet">Code pending</button>'
          : '<button class="btn btn-sm pair-recode" title="Issue a new code for this same ' +
            'connector - the companies already synced through it keep working">New code</button>') +
        '<button class="company-del icon-btn danger" title="Delete this connector">' +
        '<svg viewBox="0 0 24 24"><path d="M6 7h12l-1 13H7L6 7zm3-3h6l1 2H8l1-2zM4 6h16v2H4V6z"/></svg>' +
        "</button>";

      // New code: re-pair the SAME connector row instead of creating another one.
      // A pairing code is single-use and only its hash is stored, so the original
      // code can never be re-entered - but re-coding this row keeps its id, which
      // is what the synced companies are bound to (no re-sync needed).
      const recodeBtn = row.querySelector(".pair-recode");
      recodeBtn.addEventListener("click", async () => {
        // Replacing a code that is still unused silently invalidates it, which
        // would strand whoever is holding it - so that case asks first. The
        // confirm doubles as the way back for a code the user has lost.
        if (c.code_pending) {
          const ok = await ABSModal.confirm({
            title: "Replace the pending code?",
            bodyHtml: "<strong>" + escapeHtml(c.name) + "</strong> already has a code that " +
              "has not been used yet. Generating a new one makes the old code stop working.",
            okText: "Generate new code",
            cancelText: "Keep the old code",
          });
          if (!ok) return;
        }
        recodeBtn.disabled = true;
        try {
          const res = await window.ABSApi.post("/connector/" + c.id + "/pairing-code", {});
          h._showCode(res.pairing_code,
            "Shown once, works once. On the Tally PC open the connector and use " +
            "<strong>Re-pair</strong>, then paste this code. The companies already " +
            "synced through this connector keep working.");
        } catch (ex) {
          window.ABSToast(ex.message, true);
        } finally {
          recodeBtn.disabled = false;
        }
      });

      row.querySelector(".company-del").addEventListener("click", async () => {
        const ok = await ABSModal.confirm({
          title: "Delete connector?",
          bodyHtml: "Delete <strong>" + escapeHtml(c.name) + "</strong> from your connectors. " +
            "This does <strong>not</strong> touch Tally. This cannot be undone.",
          okText: "Delete",
          cancelText: "Cancel",
          danger: true,
        });
        if (!ok) return;
        try {
          await window.ABSApi.del("/connector/" + c.id);
          window.ABSToast("Connector deleted.");
          loadPairHistory(h);
        } catch (ex) { window.ABSToast(ex.message, true); }
      });
      list.appendChild(row);
    });
  }

  // ---- connector download ----------------------------------------------------------------------
  async function downloadConnector() {
    try {
      const res = await fetch("/api/v1/download/connector", {
        headers: { Authorization: "Bearer " + (localStorage.getItem("abs_access_token") || "") },
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Download not available yet.");
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "ABS-Tally-Connector.exe";
      a.click();
      URL.revokeObjectURL(url);
      window.ABSToast("Download started.", false, true);
    } catch (ex) {
      window.ABSToast(ex.message, true);
    }
  }
  ABSShell.downloadConnector = downloadConnector;

  // ---- desktop connector info + download modal --------------------------------------------------
  function openConnectorModal() {
    const h = ABSModal.open({
      title: "Desktop connector",
      bodyHtml:
        '<p class="muted" style="margin-top:0">The desktop connector is a small ' +
        "program that links this web app to Tally on your computer. Install it on the " +
        "PC where Tally runs - it is the only part that talks to Tally.</p>" +
        '<div class="setup-steps"><strong>How to set it up</strong>' +
        "<ol style=\"margin:8px 0 0; padding-left:20px; line-height:1.6\">" +
        "<li>Download the connector with the button below and run it on the Tally PC.</li>" +
        "<li>Click <strong>Pairing code</strong> (top right), generate a code, and paste " +
        "it into the connector window.</li>" +
        "<li>Your Tally companies appear in the connector. Click <strong>Sync ledgers</strong> " +
        "on each company you want to use here.</li>" +
        "<li>Come back and pick that company from the selector at the top right to start " +
        "creating vouchers.</li></ol></div>" +
        '<p class="muted" style="font-size:13px; margin-bottom:0">Keep Tally open with your ' +
        "company loaded while you sync.</p>",
      footHtml:
        '<button class="btn" data-act="close">Close</button>' +
        '<button class="btn btn-primary" data-act="dl">' +
        '<svg viewBox="0 0 24 24" width="16" height="16" style="fill:currentColor;vertical-align:-3px;margin-right:6px">' +
        '<path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>Download connector</button>',
    });
    h.foot.querySelector('[data-act="close"]').addEventListener("click", h.close);
    h.foot.querySelector('[data-act="dl"]').addEventListener("click", downloadConnector);
  }
  ABSShell.openConnectorModal = openConnectorModal;

  // ---- wire the static shell controls once the DOM is there -------------------------------------
  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("logout-btn").addEventListener("click", () => window.ABSAuth.logout());
    document.getElementById("pb-pair").addEventListener("click", openPairingModal);
    document.getElementById("pb-connector").addEventListener("click", openConnectorModal);
    document.getElementById("pb-upload").addEventListener("click", () => window.ABSUpload.openModal());

    const companyBtn = document.getElementById("company-btn");
    const companyMenu = document.getElementById("company-menu");
    companyBtn.addEventListener("click", async () => {
      if (companyMenu.hidden) {
        await ABSShell.refreshCompanies();
        renderCompanyMenu();
        companyMenu.hidden = false;
      } else {
        companyMenu.hidden = true;
      }
    });
    document.addEventListener("mousedown", (e) => {
      if (!companyMenu.hidden && !e.target.closest(".company-picker")) companyMenu.hidden = true;
    });

    // Sidebar toggle. The sidebar's default differs per breakpoint, so each has
    // its own class: below 900px it is hidden and "sidebar-open" reveals it;
    // above, it is shown and "sidebar-collapsed" hides it to give the content
    // the full window width.
    const isMobile = () => window.matchMedia("(max-width: 900px)").matches;
    document.getElementById("sb-toggle").addEventListener("click", () => {
      document.body.classList.toggle(isMobile() ? "sidebar-open" : "sidebar-collapsed");
    });
    // Tapping the content closes the mobile overlay only - on desktop the
    // collapsed state must survive clicking around the page.
    document.getElementById("main").addEventListener("click", () => {
      if (isMobile()) document.body.classList.remove("sidebar-open");
    });
  });

  ABSShell.onLogin = function () {
    ABSShell.refreshCompanies();
    startStatusPolling();
  };
})();
