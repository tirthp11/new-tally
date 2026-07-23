/* Vouchers: single-row grid (docs/10).
   Every voucher is its own horizontally scrolling line (.vline). Stock items,
   additional charges, GST ledgers and Round Off all sit ON the line - one
   stock/charge column per line item, count varies per invoice. Each economic
   cell shows the AI-extracted value on top with a searchable "Select ..."
   dropdown ("Create new..." opens the type-specific popup) and the amount
   under it. Draft/queued/failed only - pushed vouchers live in History. */
(function () {
  "use strict";
  window.ABSViews = window.ABSViews || {};

  const STATES = ["Andaman & Nicobar Islands", "Andhra Pradesh", "Arunachal Pradesh", "Assam",
    "Bihar", "Chandigarh", "Chhattisgarh", "Dadra & Nagar Haveli and Daman & Diu", "Delhi",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jammu & Kashmir", "Jharkhand",
    "Karnataka", "Kerala", "Ladakh", "Lakshadweep", "Madhya Pradesh", "Maharashtra",
    "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Puducherry", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh",
    "Uttarakhand", "West Bengal"];

  window.ABSViews.vouchers = async function () {
    const state = {
      rows: [],                // row controllers
      chip: "",
      query: "",
      bulkBusy: false,         // a Push/Delete selected batch is in flight
    };
    const body = document.getElementById("vg-body");
    const openId = new URLSearchParams(location.search).get("open");

    // toolbar wiring
    document.querySelectorAll(".grid-toolbar .chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        document.querySelectorAll(".grid-toolbar .chip").forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");
        state.chip = chip.dataset.chip;
        applyFilters();
      });
    });

    // Deep link from the dashboard tiles: /vouchers?status=failed opens the grid
    // with that chip already selected. Unknown values fall back to "All open".
    const statusParam = new URLSearchParams(location.search).get("status");
    if (statusParam) {
      const target = document.querySelector(
        '.grid-toolbar .chip[data-chip="' + statusParam + '"]');
      if (target) {
        document.querySelectorAll(".grid-toolbar .chip").forEach((c) => c.classList.remove("active"));
        target.classList.add("active");
        state.chip = statusParam;
      }
    }
    const searchBox = document.getElementById("vg-search");
    searchBox.addEventListener("input", () => {
      state.query = searchBox.value.trim().toUpperCase();
      applyFilters();
    });
    const allBox = document.getElementById("vg-all");
    allBox.addEventListener("change", () => {
      state.rows.forEach((r) => {
        if (!r.block.hidden && !r.removed) { r.checkbox.checked = allBox.checked; }
      });
      updateBulkButton();
    });
    document.getElementById("vg-push-selected").addEventListener("click", pushSelected);
    document.getElementById("vg-delete-selected").addEventListener("click", deleteSelected);
    function onCompanyChanged() {
      if (!document.body.contains(body)) {
        document.removeEventListener("abs:company-changed", onCompanyChanged);
        return;
      }
      reload();
    }
    document.addEventListener("abs:company-changed", onCompanyChanged);

    await reload();

    // ------------------------------------------------------------------ loading

    async function reload() {
      if (!document.body.contains(body)) return;
      const selCompany = window.ABSShell.companyId();
      if (!selCompany) {
        body.innerHTML = '<div class="empty">' +
          '<div class="empty-title">No company selected</div>' +
          "Select a company from the top-right to see its vouchers." +
          "</div>";
        state.rows = [];
        updateCount();
        return;
      }
      body.innerHTML = '<div class="empty"><span class="spinner"></span> Loading vouchers...</div>';
      state.rows = [];
      let payload;
      try {
        // One request for the whole grid: the company's masters plus every open
        // voucher with its suggestions and date preview already attached. Replaces
        // the old list + per-row detail/suggestions/date-preview fan-out (3N+4
        // requests) with a single round-trip. Rows arrive in upload order
        // (oldest first), so the Sr. numbers below follow the pick order.
        payload = await window.ABSApi.get("/vouchers/grid?company_id=" + selCompany);
      } catch (ex) { window.ABSToast(ex.message, true); return; }
      if (!document.body.contains(body)) return;  // navigated away mid-load
      const open = payload.vouchers || [];
      const masters = gridMasters(payload.masters);

      if (!open.length) {
        body.innerHTML = '<div class="empty">' +
          '<div class="empty-title">No open vouchers</div>' +
          "Upload an invoice from the top bar to create one. Pushed vouchers are in History." +
          "</div>";
        updateCount();
        return;
      }

      body.innerHTML = "";
      open.forEach((v, i) => {
        const data = {
          voucher: v,                 // already a full detail (edited_json included)
          companyId: selCompany,
          sugg: v.suggestions || null,
          masters,
          datePreview: v.date_preview || null,
        };
        const row = buildBlock(data, i + 1);
        state.rows.push(row);
        body.appendChild(row.block);
      });
      applyFilters();

      if (openId) {
        const row = state.rows.find((r) => r.voucher.id === openId);
        if (row) {
          row.block.classList.add("row-flash");
          setTimeout(() => row.block.scrollIntoView({ behavior: "smooth", block: "center" }), 80);
        }
        history.replaceState({}, "", "/vouchers");
      }
    }

    // Masters come inline with the grid payload as { ledgers, stock, units }
    // (arrays of names). Normalise so a missing key never breaks a dropdown.
    function gridMasters(m) {
      m = m || {};
      return { ledgers: m.ledgers || [], stock: m.stock || [], units: m.units || [] };
    }

    // Fresh fuzzy suggestions win, but the user's explicit EXISTING picks are kept.
    function mergeResolution(saved, fresh) {
      saved = saved || {};
      fresh = fresh || {};
      function pick(s, f) {
        if (!f) return s || null;
        if (!s) return f;
        if (s.mode === "existing") return s;
        const merged = Object.assign({}, s, {
          mode: f.mode,
          name: f.mode === "existing" ? f.name : (s.name || f.name),
        });
        if (!merged.group) merged.group = f.group;
        return merged;
      }
      function pickList(sl, fl, key) {
        sl = sl || [];
        return (fl || []).map((f) => {
          const s = sl.find((e) =>
            key === "role" ? e.role === f.role :
              key === "index" ? e.index === f.index :
                ((e.extracted || e.name || "").toUpperCase() === (f.extracted || f.name || "").toUpperCase()));
          return pick(s, f);
        });
      }
      return {
        party: pick(saved.party, fresh.party),
        purchase: pick(saved.purchase, fresh.purchase),
        units: pickList(saved.units, fresh.units),
        // Stock items merge by line index (see buildBlock), never by name.
        stock_items: pickList(saved.stock_items, fresh.stock_items, "index"),
        charges: pickList(saved.charges, fresh.charges),
        gst_ledgers: pickList(saved.gst_ledgers, fresh.gst_ledgers, "role"),
      };
    }

    // ------------------------------------------------------------------ one voucher line

    function buildBlock(data, srNo) {
      const voucher = data.voucher;
      const edited = voucher.edited_json || {};
      const invoice = edited.invoice || {};
      invoice.gst = invoice.gst || {};
      const resolution = data.sugg
        ? mergeResolution(edited.resolution, data.sugg.resolution)
        : (edited.resolution || {});
      const groups = (data.sugg && data.sugg.groups) || [];
      const taxOptions = (data.sugg && data.sugg.taxability_options) ||
        ["Taxable", "Exempt", "Nil Rated", "Non-GST"];
      const masters = data.masters;

      const row = {
        voucher, invoice, resolution, groups, taxOptions, masters,
        companyId: data.companyId,
        removed: false,
        saveTimer: null,
        savePromise: Promise.resolve(),
      };

      const block = document.createElement("div");
      block.className = "vblock status-" + voucher.status;
      row.block = block;

      const scroll = document.createElement("div");
      scroll.className = "vline-scroll";
      const line = document.createElement("div");
      line.className = "vline";
      scroll.appendChild(line);
      block.appendChild(scroll);

      // --- pinned meta (checkbox + sr + status) --------------------------------
      const meta = document.createElement("div");
      meta.className = "vcell vmeta pin-l";
      row.checkbox = document.createElement("input");
      row.checkbox.type = "checkbox";
      row.checkbox.className = "vmeta-check";
      row.checkbox.addEventListener("change", updateBulkButton);
      const srWrap = document.createElement("div");
      srWrap.className = "vmeta-body";
      srWrap.innerHTML = "<div class=\"vmeta-sr\">" + srNo + "</div>" +
        '<span class="badge ' + voucher.status + '">' + voucher.status + "</span>" +
        (voucher.uploaded_by_name
          ? '<div class="vmeta-owner" title="Uploaded by">' + escapeHtml(voucher.uploaded_by_name) + "</div>"
          : "");
      row.badge = srWrap.querySelector(".badge");
      meta.appendChild(row.checkbox);
      meta.appendChild(srWrap);
      line.appendChild(meta);

      // --- date ------------------------------------------------------------------
      const dateCell = vcell("Date");
      const dateInput = input(window.ABSFormat.date(invoice.date), "", 100);
      dateInput.addEventListener("change", () => {
        invoice.date = window.ABSFormat.dateRaw(dateInput.value);
        dateInput.value = window.ABSFormat.date(invoice.date);
        scheduleSave(row);
        refreshDateNote();
      });
      dateCell.body.appendChild(dateInput);
      const dateNote = document.createElement("div");
      dateNote.className = "cell-note";
      dateCell.body.appendChild(dateNote);
      line.appendChild(dateCell.el);

      function renderDateNote(p) {
        row.datePreview = p;
        dateNote.textContent = (p && p.changed)
          ? "Will push as " + window.ABSFormat.date(p.final_date) + " (education mode)"
          : "";
      }
      // Re-fetch only when the user edits the date; the initial preview arrives
      // with the grid payload, so first render costs no extra request.
      async function refreshDateNote() {
        if (!row.companyId) { dateNote.textContent = ""; return; }
        try {
          renderDateNote(await window.ABSApi.get(
            "/vouchers/" + voucher.id + "/date-preview?company_id=" + row.companyId));
        } catch (ex) { dateNote.textContent = ""; }
      }
      if (data.datePreview) renderDateNote(data.datePreview);
      else refreshDateNote();

      // --- supplier invoice no ---------------------------------------------------
      const invCell = vcell("Supplier Inv No");
      const invInput = input(invoice.voucher_number, "", 110);
      invInput.addEventListener("input", () => { invoice.voucher_number = invInput.value.trim(); scheduleSave(row); });
      invCell.body.appendChild(invInput);
      line.appendChild(invCell.el);

      // --- voucher type ----------------------------------------------------------
      const typeCell = vcell("Voucher Type");
      const typeSel = document.createElement("select");
      const types = ["Purchase"];
      if (invoice.voucher_type && types.indexOf(invoice.voucher_type) < 0) types.push(invoice.voucher_type);
      types.forEach((t) => {
        const o = document.createElement("option");
        o.value = t; o.textContent = t;
        if ((invoice.voucher_type || "Purchase") === t) o.selected = true;
        typeSel.appendChild(o);
      });
      typeSel.addEventListener("change", () => { invoice.voucher_type = typeSel.value; scheduleSave(row); });
      typeCell.body.appendChild(typeSel);
      line.appendChild(typeCell.el);

      // --- party -----------------------------------------------------------------
      const partyCell = vcell("Party A/C Name", "rcell");
      row.partyCell = resolveCell(partyCell.body, {
        extracted: invoice.party_name,
        entryGet: () => row.resolution.party,
        entrySet: (e) => {
          row.resolution.party = e;
          if (e && e.name) invoice.party_name = e.name;
          if (e && e._gstin) { invoice.party_gstin = e._gstin; gstinInput.value = e._gstin; }
          if (e && e._state) { invoice.party_state = e._state; setStateSel(e._state); }
        },
        options: () => masters.ledgers,
        kind: "party",
        placeholder: "Select Ledger",
        row,
      });
      line.appendChild(partyCell.el);

      // --- GSTIN -----------------------------------------------------------------
      const gstinCell = vcell("GSTIN/UIN");
      const gstinInput = input(invoice.party_gstin, "", 150);
      gstinInput.addEventListener("input", () => { invoice.party_gstin = gstinInput.value.trim(); scheduleSave(row); });
      gstinCell.body.appendChild(gstinInput);
      line.appendChild(gstinCell.el);

      // --- place of supply -------------------------------------------------------
      const stateCell = vcell("Place of Supply");
      const stateSel = document.createElement("select");
      function setStateSel(value) {
        stateSel.innerHTML = "";
        const list = STATES.slice();
        if (value && list.indexOf(value) < 0) list.unshift(value);
        const blank = document.createElement("option");
        blank.value = ""; blank.textContent = "-";
        stateSel.appendChild(blank);
        list.forEach((s) => {
          const o = document.createElement("option");
          o.value = s; o.textContent = s;
          if (s === value) o.selected = true;
          stateSel.appendChild(o);
        });
      }
      setStateSel(invoice.party_state || "");
      stateSel.addEventListener("change", () => { invoice.party_state = stateSel.value; scheduleSave(row); });
      stateCell.body.appendChild(stateSel);
      line.appendChild(stateCell.el);

      // --- total amount ----------------------------------------------------------
      // A GRN carries a Net GRN Amount that can differ from the Tax Invoice
      // total (P&F adjustments); a plain invoice leaves it at 0. Read and write
      // target the same field so an edit lands back in the value shown.
      // Mirrors _display_amount() in api/v1/vouchers.py - change both together.
      const amtField = (invoice["Net GRN Amount"] || 0) ? "Net GRN Amount" : "total_amount";
      const amtCell = vcell("Amount", "right");
      const amtInput = input(invoice[amtField], "num", 100);
      amtInput.addEventListener("input", () => {
        invoice[amtField] = parseFloat(amtInput.value) || 0;
        scheduleSave(row);
      });
      amtCell.body.appendChild(amtInput);
      line.appendChild(amtCell.el);
      
      // --- particulars (purchase ledger) -----------------------------------------
      line.appendChild(sep("Particulars"));
      const partCell = vcell("Purchase ledger", "rcell");
      const firstItem = (invoice.items || [])[0] || {};
      row.purchaseCell = resolveCell(partCell.body, {
        extracted: firstItem.purchase_ledger || "Purchase",
        entryGet: () => row.resolution.purchase,
        entrySet: (e) => {
          row.resolution.purchase = e;
          if (e && e.name) (invoice.items || []).forEach((i) => { i.purchase_ledger = e.name; });
        },
        options: () => masters.ledgers,
        kind: "purchase",
        placeholder: "Select Ledger",
        row,
      });
      line.appendChild(partCell.el);

      // --- stock items (one column each) -----------------------------------------
      const items = invoice.items || [];
      if (items.length) {
        line.appendChild(sep("Items"));
        items.forEach((item, idx) => {
          const cell = vcell("Stock item " + (idx + 1), "rcell vstock");
          resolveCell(cell.body, {
            extracted: item.stock_item_name,
            // Bind by LINE INDEX, not by name: two lines with the same extracted
            // name must stay independent so editing one never rewrites another.
            entryGet: () => (row.resolution.stock_items || [])[idx],
            entrySet: (e) => {
              e.unit = item.unit;
              e.extracted = item.stock_item_name;
              e.index = idx;
              row.resolution.stock_items = row.resolution.stock_items || [];
              row.resolution.stock_items[idx] = e;
            },
            options: () => row.masters.stock,
            kind: "stock",
            placeholder: "Select Stock Item",
            row,
          });
          // amount for this stock item
          cell.body.appendChild(subField("Amount", numInput(item, "amount", row)));
          // details toggle: qty / rate / HSN + unit dropdown
          const toggle = document.createElement("button");
          toggle.type = "button";
          toggle.className = "mini-toggle";
          toggle.textContent = "Details";
          const panel = document.createElement("div");
          panel.className = "vdetails";
          panel.hidden = true;
          panel.appendChild(subField("Qty", numInput(item, "quantity", row)));
          panel.appendChild(subField("Rate", numInput(item, "rate", row)));
          const hsn = input(item.hsn_sac, "", 90);
          hsn.addEventListener("input", () => { item.hsn_sac = hsn.value.trim(); scheduleSave(row); });
          panel.appendChild(subField("HSN/SAC", hsn));
          const unitWrap = document.createElement("div");
          unitWrap.className = "vsub";
          const unitLbl = document.createElement("label");
          unitLbl.textContent = "Unit";
          unitWrap.appendChild(unitLbl);
          resolveCell(unitWrap, {
            extracted: item.unit,
            entryGet: () => findEntry(row.resolution.units, item.unit),
            entrySet: (e) => { putEntry(row.resolution, "units", e, item.unit); },
            options: () => row.masters.units,
            kind: "unit",
            placeholder: "Select Unit",
            row,
          });
          panel.appendChild(unitWrap);
          toggle.addEventListener("click", () => { panel.hidden = !panel.hidden; });
          cell.body.appendChild(toggle);
          cell.body.appendChild(panel);
          line.appendChild(cell.el);
        });
      }

      // --- additional charges (one column each) ----------------------------------
      // Round Off is rendered in its own section below, never as a charge column
      // (it stays in additional_charges because that is what posts to Tally).
      const charges = (invoice.additional_charges || []).filter(
        (c) => !isRoundOffName(c.ledger_name));
      if (charges.length) {
        line.appendChild(sep("Charges"));
        charges.forEach((charge, idx) => {
          const cell = vcell("Charge " + (idx + 1), "rcell");
          resolveCell(cell.body, {
            extracted: charge.ledger_name,
            entryGet: () => findEntry(row.resolution.charges, charge.ledger_name),
            entrySet: (e) => {
              e.amount = charge.amount;
              putEntry(row.resolution, "charges", e, charge.ledger_name);
            },
            options: () => row.masters.ledgers,
            kind: "charge",
            placeholder: "Select Ledger",
            row,
          });
          cell.body.appendChild(subField("Amount", numInput(charge, "amount", row)));
          line.appendChild(cell.el);
        });
      }

      // --- GST ledgers (only taxes with an amount) -------------------------------
      const gst = invoice.gst || {};
      const roles = [["cgst_ledger", "cgst_amount", "Input CGST"],
      ["sgst_ledger", "sgst_amount", "Input SGST"],
      ["igst_ledger", "igst_amount", "Input IGST"]];
      const activeGst = roles.filter(([, ak]) => (parseFloat(gst[ak]) || 0) > 0);
      if (activeGst.length) {
        line.appendChild(sep("GST"));
        activeGst.forEach(([lkey, akey, lbl]) => {
          const cell = vcell(lbl, "rcell");
          resolveCell(cell.body, {
            extracted: gst[lkey],
            entryGet: () => (row.resolution.gst_ledgers || []).find((e) => e.role === lkey),
            entrySet: (e) => {
              e.role = lkey;
              row.resolution.gst_ledgers = row.resolution.gst_ledgers || [];
              const i = row.resolution.gst_ledgers.findIndex((x) => x.role === lkey);
              if (i >= 0) row.resolution.gst_ledgers[i] = e; else row.resolution.gst_ledgers.push(e);
            },
            options: () => row.masters.ledgers,
            kind: "gst",
            placeholder: "Select Ledger",
            row,
          });
          cell.body.appendChild(subField("Amount", numInput(gst, akey, row)));
          line.appendChild(cell.el);
        });
      }

      // --- round off -------------------------------------------------------------
      // The round off that actually posts to Tally is the additional_charges
      // entry (the top-level "Round off" field is verification-only). Show that
      // entry here with a ledger dropdown + amount so there is exactly one Round
      // Off control, at the end; keep the top-level field mirrored to it.
      line.appendChild(sep("Round Off"));
      const roCharge = (invoice.additional_charges || []).find(
        (c) => isRoundOffName(c.ledger_name));
      if (roCharge) {
        const roCell = vcell("Round off", "rcell");
        resolveCell(roCell.body, {
          extracted: roCharge.ledger_name,
          entryGet: () => findEntry(row.resolution.charges, roCharge.ledger_name),
          entrySet: (e) => {
            e.amount = roCharge.amount;
            putEntry(row.resolution, "charges", e, roCharge.ledger_name);
          },
          options: () => row.masters.ledgers,
          kind: "charge",
          placeholder: "Select Ledger",
          row,
        });
        const roAmt = numInput(roCharge, "amount", row);
        roAmt.addEventListener("input", () => { invoice["Round off"] = roCharge.amount; });
        roCell.body.appendChild(subField("Amount", roAmt));
        line.appendChild(roCell.el);
      } else {
        // No round off on this invoice: keep the simple amount field bound to the
        // top-level "Round off" value (a zero round off is never pushed).
        const roCell = vcell("Round off", "right");
        roCell.body.appendChild(numInput(invoice, "Round off", row));
        line.appendChild(roCell.el);
      }

      // --- narration -------------------------------------------------------------
      // Narration is prose and usually longer than the box on the line, so the box
      // stays a plain inline field (edit short notes without leaving the grid) and
      // "Show" opens the full text in a popup. Both views write the same
      // invoice.narration and autosave on the shared debounce, exactly like every
      // other field here - so closing the popup can never lose an edit.
      const narrCell = vcell("Narration");
      const narrInput = input(invoice.narration, "", 160);
      narrInput.title = invoice.narration || "";  // full value on hover
      narrInput.addEventListener("input", () => {
        invoice.narration = narrInput.value;
        narrInput.title = narrInput.value;
        scheduleSave(row);
      });
      const narrShow = document.createElement("button");
      narrShow.type = "button";
      narrShow.className = "mini-toggle";
      narrShow.textContent = "Show";
      narrShow.addEventListener("click", () => {
        // ABSModal already renders the title bar with its own top-right "x" and
        // closes on Escape (shell.js), so the footer only adds Close and Save.
        const h = window.ABSModal.open({
          title: "Narration",
          bodyHtml: '<textarea class="narr-area" rows="8"></textarea>',
          footHtml:
            '<button class="btn" data-act="close">Close</button>' +
            '<button class="btn btn-primary" data-act="save">Save</button>',
        });
        const area = h.body.querySelector(".narr-area");
        // Assigned, never interpolated into bodyHtml: narration is free text and
        // a literal "</textarea>" in it would otherwise break out of the element.
        area.value = invoice.narration == null ? "" : invoice.narration;
        area.addEventListener("input", () => {
          invoice.narration = area.value;
          narrInput.value = area.value;
          narrInput.title = area.value;
          scheduleSave(row);
        });
        h.foot.querySelector('[data-act="close"]').addEventListener("click", () => h.close());
        const saveBtn = h.foot.querySelector('[data-act="save"]');
        saveBtn.addEventListener("click", async () => {
          clearTimeout(row.saveTimer);  // commit now instead of on the debounce
          saveBtn.disabled = true;
          try {
            await saveRow(row);
            h.close();
            window.ABSToast("Narration saved.");
          } finally {
            saveBtn.disabled = false;
          }
        });
      });
      narrCell.body.appendChild(narrInput);
      narrCell.body.appendChild(narrShow);
      line.appendChild(narrCell.el);

      // --- actions (pinned right) ------------------------------------------------
      const actCell = document.createElement("div");
      actCell.className = "vcell vact pin-r";
      const pushBtn = document.createElement("button");
      pushBtn.className = "btn btn-primary btn-sm";
      pushBtn.textContent = voucher.status === "failed" ? "Retry push" : "Push to Tally";
      pushBtn.addEventListener("click", () => pushRow(row, pushBtn));
      const delBtn = document.createElement("button");
      delBtn.className = "icon-btn danger";
      delBtn.title = "Delete (web app only - Tally is never touched)";
      delBtn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>';
      delBtn.addEventListener("click", () => deleteRow(row));
      actCell.appendChild(pushBtn);
      actCell.appendChild(document.createTextNode(" "));
      actCell.appendChild(delBtn);
      row.pushBtn = pushBtn;
      line.appendChild(actCell);

      // --- error sub-line (only thing not on the line - it is prose) -------------
      row.showError = function () {
        const old = block.querySelector(".vline-error");
        if (old) old.remove();
        if (row.voucher.status === "failed" && row.voucher.error_log) {
          const err = document.createElement("div");
          err.className = "vline-error";
          err.appendChild(errorBox(row.voucher.error_log));
          block.appendChild(err);
        }
      };
      row.showError();
      return row;
    }

    // ------------------------------------------------------------------ cell helpers

    function vcell(label, extra) {
      const el = document.createElement("div");
      el.className = "vcell" + (extra ? " " + extra : "");
      if (label) {
        const cap = document.createElement("div");
        cap.className = "vcap";
        cap.textContent = label;
        el.appendChild(cap);
      }
      const bodyEl = document.createElement("div");
      bodyEl.className = "vbody";
      el.appendChild(bodyEl);
      return { el, body: bodyEl };
    }
    function sep(title) {
      const el = document.createElement("div");
      el.className = "vsep";
      el.innerHTML = "<span>" + escapeHtml(title) + "</span>";
      return el;
    }
    function subField(label, node) {
      const el = document.createElement("div");
      el.className = "vsub";
      const lbl = document.createElement("label");
      lbl.textContent = label;
      el.appendChild(lbl);
      el.appendChild(node);
      return el;
    }
    function numInput(obj, key, row) {
      const el = input(obj[key], "num", 90);
      el.addEventListener("input", () => { obj[key] = parseFloat(el.value) || 0; scheduleSave(row); });
      return el;
    }
    function input(value, cls, width) {
      const el = document.createElement("input");
      el.value = value == null ? "" : value;
      if (cls) el.className = cls;
      if (width) el.style.minWidth = width + "px";
      return el;
    }

    /* extracted value + searchable dropdown inside one cell */
    function resolveCell(container, cfg) {
      const wrap = document.createElement("div");
      const label = document.createElement("div");
      label.className = "extracted";
      wrap.appendChild(label);

      const entry = cfg.entryGet();
      const dd = window.ABSDropdown({
        placeholder: cfg.placeholder,
        value: entry ? { mode: entry.mode, name: entry.name } : null,
        best: entry && entry.mode === "existing" ? entry.name : null,
        options: cfg.options(),
        onPick: (name) => {
          const cur = cfg.entryGet() || {};
          const next = Object.assign({}, cur, { mode: "existing", name: name, extracted: cfg.extracted });
          cfg.entrySet(next);
          refreshLabel();
          scheduleSave(cfg.row);
        },
        onCreate: async () => {
          const cur = cfg.entryGet() || { name: cfg.extracted };
          const ctx = {
            groups: cfg.row.groups,
            taxOptions: cfg.row.taxOptions,
            units: cfg.row.masters.units,
            gstin: cfg.row.invoice.party_gstin,
            state: cfg.row.invoice.party_state,
          };
          const out = await window.ABSMasterModal.open(cfg.kind, Object.assign({ extracted: cfg.extracted }, cur), ctx);
          if (out) {
            out.extracted = cfg.extracted;
            cfg.entrySet(out);
            dd._setValue({ mode: "new", name: out.name });
            refreshLabel();
            scheduleSave(cfg.row);
          }
        },
      });
      wrap.appendChild(dd);
      container.appendChild(wrap);

      function refreshLabel() {
        const e = cfg.entryGet();
        const resolved = e && e.mode === "existing" && e.name;
        label.className = "extracted" + (resolved ? " resolved" : "");
        label.innerHTML = escapeHtml(cfg.extracted || "-") +
          (resolved ? "" :
            ' <span class="warn-ico" title="No existing Tally master picked - a new one will be created on push">*</span>');
      }
      refreshLabel();
      return { refreshLabel, dropdown: dd };
    }

    function isRoundOffName(name) {
      // Matches "Round Off" / "Round-off" / "ROUNDOFF" etc. Used to keep round
      // off out of the Charges columns and render it in its own section instead.
      return (name || "").toLowerCase().replace(/[^a-z]/g, "").indexOf("roundoff") >= 0;
    }
    function findEntry(list, extracted) {
      return (list || []).find((e) =>
        (e.extracted || e.name || "").toUpperCase() === (extracted || "").toUpperCase()) || null;
    }
    function putEntry(resolution, key, entry, extracted) {
      resolution[key] = resolution[key] || [];
      const i = resolution[key].findIndex((e) =>
        (e.extracted || e.name || "").toUpperCase() === (extracted || "").toUpperCase());
      if (i >= 0) resolution[key][i] = entry; else resolution[key].push(entry);
    }

    function errorBox(errorLog) {
      const stage = errorLog.stage || "voucher";
      const stageText = {
        precheck: "Blocked before sending anything to Tally",
        masters: "Creating masters in Tally failed",
        voucher: "Tally rejected the voucher",
        connector: "Could not reach the desktop connector",
      }[stage] || "Push failed";
      const box = document.createElement("div");
      box.className = "push-error";
      const errs = errorLog.errors || [];
      box.innerHTML =
        '<div class="pe-head">' + stageText + "</div>" +
        "<ul>" + errs.map((e) => "<li>" + escapeHtml(friendly(e)) + "</li>").join("") + "</ul>" +
        (errs.length ? "<details><summary>Raw details</summary><div>" +
          errs.map((e) => escapeHtml(e)).join("<br>") + "</div></details>" : "");
      return box;
    }
    function friendly(err) {
      const e = String(err);
      if (/does not exist/i.test(e)) return e + " - the master is missing in Tally. Pick or create it in the dropdown.";
      if (/Unknown Request/i.test(e)) return "Tally could not read the request. Retry; if it repeats, report this error.";
      return e;
    }

    // ------------------------------------------------------------------ save / push / delete

    function scheduleSave(row) {
      clearTimeout(row.saveTimer);
      row.saveTimer = setTimeout(() => { row.savePromise = saveRow(row); }, 900);
    }

    async function saveRow(row) {
      if (row.removed) return;
      try {
        if (row.resolution.party && row.resolution.party.name) {
          row.invoice.party_name = row.invoice.party_name || row.resolution.party.name;
        }
        row.voucher = await window.ABSApi.put("/vouchers/" + row.voucher.id, {
          edited_json: { invoice: row.invoice, resolution: row.resolution },
          company_id: row.companyId || null,
        });
      } catch (ex) {
        window.ABSToast("Autosave failed: " + ex.message, true);
      }
    }

    async function pushRow(row, btn) {
      if (!row.companyId) {
        window.ABSToast("This voucher has no company. Select a company (top right) and re-open Vouchers.", true);
        return;
      }
      // Re-entrancy guard: a second click (or the bulk pool reaching an already
      // in-flight row) must not fire a second POST /push. Without this the
      // backend can still see status="draft" on the duplicate request and push
      // the same voucher to Tally twice.
      if (row.pushing) return;
      row.pushing = true;
      try {
        await pushRowInner(row, btn);
      } finally {
        row.pushing = false;
      }
    }

    async function pushRowInner(row, btn) {
      clearTimeout(row.saveTimer);
      await saveRow(row);

      // No date-change prompt: education mode silently coerces the invoice date
      // to an allowed day (e.g. 1st) and pushes straight away. The backend
      // applies the exact same coercion when confirmed_date is null
      // (push_service.py: invoice["date"] = confirmed_date or preview final),
      // so the Tally result is identical - we just skip asking.
      btn.disabled = true;
      const oldText = btn.textContent;
      btn.innerHTML = '<span class="spinner"></span> Pushing...';
      try {
        const result = await window.ABSApi.post("/vouchers/" + row.voucher.id + "/push", {
          company_id: row.companyId,
          confirmed_date: null,
        });
        if (result.ok) {
          window.ABSToast("Pushed to Tally" +
            (result.tally_voucher_number ? " (voucher " + result.tally_voucher_number + ")" : "") +
            ". Moved to History.", false, true);
          row.removed = true;
          row.block.classList.add("leaving");
          setTimeout(() => { row.block.remove(); updateCount(); }, 320);
        } else {
          row.voucher.status = "failed";
          row.voucher.error_log = { stage: result.stage, errors: result.errors || [] };
          row.block.className = "vblock status-failed";
          row.badge.className = "badge failed";
          row.badge.textContent = "failed";
          btn.textContent = "Retry push";
          row.showError();
          window.ABSToast("Push failed - see the reason under the row.", true);
        }
      } catch (ex) {
        window.ABSToast(ex.message, true);
      } finally {
        btn.disabled = false;
        if (btn.querySelector(".spinner")) btn.textContent = oldText === "Push to Tally" && row.voucher.status === "failed" ? "Retry push" : oldText;
      }
      updateCount();
    }

    async function deleteRow(row) {
      const ok = await window.ABSModal.confirm({
        title: "Delete voucher",
        body: "This removes the record from this web app's database only. " +
          "Nothing is deleted or changed in Tally. Delete it?",
        okText: "Delete", danger: true,
      });
      if (!ok) return;
      try {
        await window.ABSApi.del("/vouchers/" + row.voucher.id +
          (row.companyId ? "?company_id=" + row.companyId : ""));
        row.removed = true;
        row.block.remove();
        updateCount();
        window.ABSToast("Deleted from the web app. Tally untouched.");
      } catch (ex) {
        window.ABSToast(ex.message, true);
      }
    }

    // How many vouchers push at once. Each push generates its XML (a slow AI
    // call) in parallel; the backend still serializes the actual Tally write per
    // connector, so this only overlaps the generation, never the Tally import.
    const MAX_CONCURRENT_PUSH = 4;

    async function pushSelected() {
      // Buffer the whole batch: the button goes disabled + "Pushing..." the
      // instant it is clicked and stays that way until every selected voucher is
      // done. A second click while the XML is still generating would otherwise
      // re-push the same rows (the backend still sees status="draft") and create
      // duplicates in Tally. state.bulkBusy is set before the first await, so the
      // re-click lands on an already-disabled button. See updateBulkButton, which
      // must honour the flag or it would re-enable the button between rows.
      if (state.bulkBusy) return;
      const selected = state.rows.filter((r) => !r.removed && r.checkbox.checked && !r.block.hidden);
      if (!selected.length) return;
      const pushBtn = document.getElementById("vg-push-selected");
      state.bulkBusy = true;
      pushBtn.disabled = true;
      document.getElementById("vg-delete-selected").disabled = true;
      pushBtn.innerHTML = '<span class="spinner"></span> Pushing...';
      try {
        // Bounded worker pool: keep up to MAX_CONCURRENT_PUSH pushes in flight and
        // pull the next voucher as each finishes. pushRow already owns its own
        // button/spinner/status, so concurrent rows update independently.
        let next = 0;
        async function worker() {
          while (next < selected.length) {
            const row = selected[next++];
            if (row.removed) continue;
            await pushRow(row, row.pushBtn);
          }
        }
        const workers = [];
        for (let i = 0; i < Math.min(MAX_CONCURRENT_PUSH, selected.length); i++) {
          workers.push(worker());
        }
        await Promise.allSettled(workers);
      } finally {
        state.bulkBusy = false;
        if (pushBtn.isConnected) pushBtn.textContent = "Push selected";
        allBox.checked = false;
        updateBulkButton();  // re-enables only if any rows are still selectable
      }
    }

    // ------------------------------------------------------------------ filters

    function applyFilters() {
      state.rows.forEach((row) => {
        if (row.removed) return;
        const inv = row.invoice;
        const hay = [
          window.ABSFormat.date(inv.date),
          inv.voucher_number || "",
          inv.party_name || "",
          inv.party_gstin || "",
          (row.resolution.purchase && row.resolution.purchase.name) || "",
          inv.narration || "",
        ].join(" ").toUpperCase();
        let show = !state.chip || row.voucher.status === state.chip;
        if (state.query && hay.indexOf(state.query) < 0) show = false;
        row.block.hidden = !show;
      });
      updateCount();
    }

    function updateCount() {
      // A late push/delete callback can run after the user left this view, when
      // vg-count no longer exists; guard so it never throws on a null element.
      const countEl = document.getElementById("vg-count");
      if (!countEl) return;
      const live = state.rows.filter((r) => !r.removed);
      const visible = live.filter((r) => !r.block.hidden);
      countEl.textContent =
        live.length ? visible.length + " of " + live.length + " vouchers" : "";
      updateBulkButton();
    }

    function updateBulkButton() {
      const pushBtn = document.getElementById("vg-push-selected");
      if (!pushBtn) return;
      const delBtn = document.getElementById("vg-delete-selected");
      // While a batch runs, both stay disabled regardless of selection - this
      // call fires between rows (via updateCount) and would otherwise re-enable
      // the button mid-push, defeating the buffer.
      if (state.bulkBusy) {
        pushBtn.disabled = true;
        if (delBtn) delBtn.disabled = true;
        return;
      }
      const any = state.rows.some((r) => !r.removed && !r.block.hidden && r.checkbox.checked);
      pushBtn.disabled = !any;
      if (delBtn) delBtn.disabled = !any;
    }

    async function deleteSelected() {
      // Same buffer as pushSelected: guard from the top so a second click cannot
      // open a second confirm or run the delete loop twice. The flag is cleared
      // in finally, including when the user cancels the confirm.
      if (state.bulkBusy) return;
      const selected = state.rows.filter((r) => !r.removed && r.checkbox.checked && !r.block.hidden);
      if (!selected.length) return;
      state.bulkBusy = true;
      updateBulkButton();  // disables both while the confirm is open
      try {
        const ok = await window.ABSModal.confirm({
          title: "Delete selected vouchers",
          body: "This removes " + selected.length + " voucher(s) from this web app's " +
            "database only. Nothing is deleted or changed in Tally. Delete them?",
          okText: "Delete " + selected.length, danger: true,
        });
        if (!ok) return;
        const failed = [];
        for (const row of selected) {
          if (row.removed) continue;
          try {
            await window.ABSApi.del("/vouchers/" + row.voucher.id +
              (row.companyId ? "?company_id=" + row.companyId : ""));
            row.removed = true;
            row.block.remove();
          } catch (ex) {
            failed.push(ex.message);
          }
        }
        allBox.checked = false;
        if (failed.length) {
          window.ABSToast(failed.length + " could not be deleted: " + failed[0], true);
        } else {
          window.ABSToast("Deleted from the web app. Tally untouched.");
        }
      } finally {
        state.bulkBusy = false;
        updateCount();  // recount + re-enable buttons per remaining selection
      }
    }
  };
})();
