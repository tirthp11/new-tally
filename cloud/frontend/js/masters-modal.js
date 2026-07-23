/* Type-specific "Create new" popups for the voucher grid dropdowns.
   window.ABSMasterModal.open(kind, entry, ctx) -> Promise<entry | null>
   kind: party | purchase | charge | gst | stock | unit
   entry: current resolution entry (name prefilled from the extracted value)
   ctx:   { groups: [...], taxOptions: [...], units: [...] }
   Save resolves with the new entry (mode "new"); Close/X/Esc resolves null. */
(function () {
  "use strict";

  const TITLES = {
    party: "Create new party ledger",
    purchase: "Create new purchase ledger",
    charge: "Create new charge ledger",
    gst: "Create new GST ledger",
    stock: "Create new stock item",
    unit: "Create new unit",
  };

  const DEFAULT_GROUP = {
    party: "Sundry Creditors",
    purchase: "Purchase Accounts",
    charge: "Indirect Expenses",
    gst: "Duties & Taxes",
  };

  function options(list, selected) {
    return (list || []).map((g) =>
      '<option value="' + escapeHtml(g) + '"' + (g === selected ? " selected" : "") + ">" +
      escapeHtml(g) + "</option>").join("");
  }

  function open(kind, entry, ctx) {
    entry = entry || {};
    ctx = ctx || {};
    return new Promise((resolve) => {
      let done = false;
      const groupSel = entry.group || DEFAULT_GROUP[kind];

      let fields =
        '<div class="field"><label>Name</label>' +
        '<input id="mm-name" value="' + escapeHtml(entry.name || entry.extracted || "") + '"></div>';

      if (kind === "party" || kind === "purchase" || kind === "charge" || kind === "gst") {
        fields +=
          '<div class="field"><label>Group (parent in Tally)</label>' +
          '<select id="mm-group">' + options(ctx.groups, groupSel) + "</select></div>";
      }
      if (kind === "party") {
        fields +=
          '<div class="field"><label>GSTIN (optional)</label>' +
          '<input id="mm-gstin" value="' + escapeHtml(ctx.gstin || "") + '"></div>' +
          '<div class="field"><label>State (optional)</label>' +
          '<input id="mm-state" value="' + escapeHtml(ctx.state || "") + '"></div>';
      }
      if (kind === "charge") {
        fields +=
          '<div class="field"><label style="display:flex;align-items:center;gap:8px;color:var(--ink)">' +
          '<input type="checkbox" id="mm-gstapp" style="width:auto"' +
          (entry.appropriate_for_gst ? " checked" : "") + ">" +
          "Appropriate for GST (Goods, Based on Value)</label></div>";
      }
      if (kind === "stock") {
        fields +=
          '<div class="field"><label>Unit</label>' +
          '<select id="mm-unit">' + options(ctx.units, entry.unit) +
          (ctx.units && ctx.units.indexOf(entry.unit) >= 0 || !entry.unit ? "" :
            '<option value="' + escapeHtml(entry.unit) + '" selected>' + escapeHtml(entry.unit) + " (new)</option>") +
          "</select></div>" +
          '<div class="row">' +
          '<div class="field" style="flex:1"><label>Taxability</label>' +
          '<select id="mm-tax">' + options(ctx.taxOptions || ["Taxable", "Exempt", "Nil Rated", "Non-GST"],
            entry.taxability || "Taxable") + "</select></div>" +
          '<div class="field" style="flex:1"><label>Total GST %</label>' +
          '<input id="mm-rate" value="' + (entry.gst_rate == null ? "" : entry.gst_rate) + '"></div>' +
          '<div class="field" style="flex:1"><label>HSN</label>' +
          '<input id="mm-hsn" value="' + escapeHtml(entry.hsn || "") + '"></div>' +
          "</div>";
      }
      if (kind === "unit") {
        fields +=
          '<p class="muted" style="font-size:13px;margin-top:0">The symbol as it should appear in Tally, e.g. Nos, Kgs, Mtr.</p>';
      }

      const h = window.ABSModal.open({
        title: TITLES[kind] || "Create new",
        bodyHtml: fields + '<div id="mm-err" class="field-error"></div>',
        footHtml:
          '<button class="btn" data-act="close">Close</button>' +
          '<button class="btn btn-primary" data-act="save">Save</button>',
        onClose: () => { if (!done) { done = true; resolve(null); } },
      });

      h.foot.querySelector('[data-act="close"]').addEventListener("click", h.close);
      h.foot.querySelector('[data-act="save"]').addEventListener("click", () => {
        const name = h.body.querySelector("#mm-name").value.trim();
        if (!name) {
          h.body.querySelector("#mm-err").textContent = "Name is required.";
          return;
        }
        const out = Object.assign({}, entry, { mode: "new", name: name });
        const g = h.body.querySelector("#mm-group");
        if (g) out.group = g.value;
        if (kind === "charge") {
          out.appropriate_for_gst = h.body.querySelector("#mm-gstapp").checked;
        }
        if (kind === "party") {
          out._gstin = h.body.querySelector("#mm-gstin").value.trim();
          out._state = h.body.querySelector("#mm-state").value.trim();
        }
        if (kind === "stock") {
          out.unit = h.body.querySelector("#mm-unit").value;
          out.taxability = h.body.querySelector("#mm-tax").value;
          const rate = h.body.querySelector("#mm-rate").value.trim();
          out.gst_rate = rate === "" ? null : parseFloat(rate);
          out.hsn = h.body.querySelector("#mm-hsn").value.trim();
        }
        done = true;
        h.close();
        resolve(out);
      });
    });
  }

  window.ABSMasterModal = { open };
})();
