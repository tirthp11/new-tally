/* History: vouchers already pushed to Tally. Deleting here removes the row
   from THIS database only - the voucher in Tally is never touched. */
(function () {
  "use strict";
  window.ABSViews = window.ABSViews || {};

  window.ABSViews.history = async function () {
    const tbody = document.getElementById("hist-body");
    const search = document.getElementById("hist-search");
    const clearAllBtn = document.getElementById("hist-clear-all");
    let vouchers = [];
    let companyId = "";
    // Year and month filter independently, so "All years + July" means July of
    // every year. Set by the dashboard's "Pushed this month" tile, which links to
    // /history?year=YYYY&month=MM.
    const params = new URLSearchParams(location.search);
    let yearFilter = params.get("year") || "";    // "2026" or "" for all years
    let monthFilter = params.get("month") || "";  // "07" or "" for all months

    // Wrap render() so the InputEvent is not passed as its `noCompany` argument
    // (an Event object is truthy and would trip the "No company selected" branch).
    search.addEventListener("input", () => render());
    clearAllBtn.addEventListener("click", clearAll);

    const yearSelect = document.getElementById("hist-year");
    const monthSelect = document.getElementById("hist-month");

    // Months are the fixed twelve; the year list is rebuilt from the data after
    // each load (see refreshYearOptions), so a new year appears by itself.
    buildMonthOptions();
    monthSelect.value = monthFilter;
    yearSelect.addEventListener("change", () => {
      yearFilter = yearSelect.value;
      syncUrl();
      render();
    });
    monthSelect.addEventListener("change", () => {
      monthFilter = monthSelect.value;
      syncUrl();
      render();
    });

    // Keep the URL in step so a refresh or shared link keeps both selections.
    function syncUrl() {
      const q = [];
      if (yearFilter) q.push("year=" + yearFilter);
      if (monthFilter) q.push("month=" + monthFilter);
      history.replaceState({}, "", q.length ? "/history?" + q.join("&") : "/history");
    }

    function monthName(mm) {
      return new Date(2000, Number(mm) - 1, 1).toLocaleString(undefined, { month: "long" });
    }

    function buildMonthOptions() {
      monthSelect.appendChild(newOption("", "All months"));
      for (let m = 1; m <= 12; m++) {
        const value = String(m).padStart(2, "0");
        monthSelect.appendChild(newOption(value, monthName(value)));
      }
    }

    // How the current year/month selection reads in a sentence: "July 2026",
    // "2026", "July of every year", or "" when nothing is filtered.
    function periodLabel() {
      if (yearFilter && monthFilter) return monthName(monthFilter) + " " + yearFilter;
      if (yearFilter) return yearFilter;
      if (monthFilter) return monthName(monthFilter) + " of any year";
      return "";
    }

    function newOption(value, text) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = text;
      return opt;
    }

    // Years actually present in the pushed vouchers, newest first, always
    // including the current year (so a fresh company is never an empty list) and
    // any year that was linked to - a linked year missing from the list would
    // silently snap to "All years" and disagree with the rows on screen.
    function refreshYearOptions() {
      const years = {};
      vouchers.forEach((v) => {
        const y = (v.pushed_at || "").slice(0, 4);
        if (y) years[y] = true;
      });
      years[String(new Date().getFullYear())] = true;
      if (yearFilter) years[yearFilter] = true;

      const sorted = Object.keys(years).sort().reverse();
      yearSelect.innerHTML = "";
      yearSelect.appendChild(newOption("", "All years"));
      sorted.forEach((y) => yearSelect.appendChild(newOption(y, y)));
      yearSelect.value = yearFilter;  // preserved across rebuilds
    }
    function onCompanyChanged() {
      if (!document.body.contains(tbody)) {
        document.removeEventListener("abs:company-changed", onCompanyChanged);
        return;
      }
      load();
    }
    document.addEventListener("abs:company-changed", onCompanyChanged);
    await load();

    async function load() {
      companyId = window.ABSShell.companyId();
      if (!companyId) {
        vouchers = [];
        refreshYearOptions();
        render(true);
        return;
      }
      try {
        // sort=pushed_desc: most recent push to Tally first. History sorts on
        // pushed_at - the same field the Pushed column shows and the year/month
        // filters read - so the row order always agrees with that column.
        vouchers = await window.ABSApi.get(
          "/vouchers?status_filter=pushed&sort=pushed_desc&company_id=" + companyId);
      } catch (ex) {
        window.ABSToast(ex.message, true);
        vouchers = [];
      }
      // Rebuilt per load: switching company changes which years exist.
      refreshYearOptions();
      render();
    }

    // The currently matching rows: everything when the search box is empty,
    // otherwise the party/invoice-number matches. Shared by render() (display)
    // and clearAll() (which deletes exactly what is shown).
    function filteredVouchers() {
      // Year and month apply independently against pushed_at (the same field the
      // dashboard counts): "2026-07-20T..." -> year 0-4, month 5-7. With no year
      // selected, a month matches that month in every year.
      let list = vouchers.slice();
      if (yearFilter) {
        list = list.filter((v) => (v.pushed_at || "").slice(0, 4) === yearFilter);
      }
      if (monthFilter) {
        list = list.filter((v) => (v.pushed_at || "").slice(5, 7) === monthFilter);
      }
      const f = search.value.trim().toUpperCase();
      if (!f) return list;
      return list.filter((v) =>
        (v.party_name || "").toUpperCase().indexOf(f) >= 0 ||
        (v.invoice_number || "").toUpperCase().indexOf(f) >= 0);
    }

    function render(noCompany) {
      // load()/delete callbacks can fire after the user left History; if the
      // table is gone, hist-count is too and writing it would throw on null.
      if (!document.body.contains(tbody)) return;
      if (noCompany) {
        tbody.innerHTML = '<tr><td colspan="7"><div class="empty">' +
          '<div class="empty-title">No company selected</div>' +
          "Select a company from the top-right to see its pushed vouchers." +
          "</div></td></tr>";
        document.getElementById("hist-count").textContent = "";
        clearAllBtn.disabled = true;
        return;
      }
      const list = filteredVouchers();

      tbody.innerHTML = "";
      list.forEach((v) => {
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + escapeHtml(v.party_name) + "</td>" +
          "<td>" + escapeHtml(v.invoice_number) + "</td>" +
          "<td>" + escapeHtml(window.ABSFormat.date(v.invoice_date)) + "</td>" +
          '<td class="right">' + window.ABSFormat.money(v.amount) + "</td>" +
          "<td>" + (v.pushed_at ? new Date(v.pushed_at).toLocaleString() : "-") + "</td>" +
          "<td>" + escapeHtml(v.tally_voucher_number) + "</td>" +
          "<td></td>";
        const del = document.createElement("button");
        del.className = "icon-btn danger";
        del.title = "Delete from this web app (Tally is never touched)";
        del.innerHTML = '<svg viewBox="0 0 24 24"><path d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>';
        del.addEventListener("click", async () => {
          const ok = await window.ABSModal.confirm({
            title: "Delete from history",
            body: "This removes the record from this web app's database only. " +
              "The voucher already imported into Tally is NOT touched. Delete it?",
            okText: "Delete", danger: true,
          });
          if (!ok) return;
          try {
            await window.ABSApi.del("/vouchers/" + v.id +
              (companyId ? "?company_id=" + companyId : ""));
            vouchers = vouchers.filter((x) => x.id !== v.id);
            render();
            window.ABSToast("Removed from history. Tally untouched.");
          } catch (ex) {
            window.ABSToast(ex.message, true);
          }
        });
        tr.lastElementChild.appendChild(del);
        tbody.appendChild(tr);
      });

      document.getElementById("hist-count").textContent =
        vouchers.length ? list.length + " of " + vouchers.length + " pushed vouchers" : "";
      clearAllBtn.disabled = !list.length;
      if (!list.length) {
        const period = periodLabel();
        const narrowed = vouchers.length && (period || search.value.trim());
        tbody.innerHTML = '<tr><td colspan="7"><div class="empty">' +
          '<div class="empty-title">' +
          (vouchers.length ? "No match" : "Nothing pushed yet") + "</div>" +
          (narrowed
            ? (period ? "Nothing was pushed in " + period +
              '. Pick "All years" and "All months" to see every pushed voucher.'
              : "Try a different search.")
            : "Vouchers appear here after a successful push to Tally.") +
          "</div></td></tr>";
      }
    }

    // Clear all: when a search is active, delete only the matching rows (option B);
    // with no search, delete every loaded history row (option A). Web-app only -
    // the vouchers already imported into Tally are never touched.
    async function clearAll() {
      const targets = filteredVouchers();
      if (!targets.length) return;
      // A year/month filter narrows this the same way a search does - say so, so
      // the user is never surprised about which rows go.
      const narrowed = !!search.value.trim() || !!yearFilter || !!monthFilter;
      const ok = await window.ABSModal.confirm({
        title: "Clear history",
        body: "This removes " + targets.length +
          (narrowed ? " currently shown record(s)" : " record(s)") +
          " from this web app's database only. The vouchers already imported " +
          "into Tally are NOT touched. Continue?",
        okText: "Clear " + targets.length, danger: true,
      });
      if (!ok) return;

      clearAllBtn.disabled = true;
      const ids = targets.map((v) => v.id);
      const failed = [];
      for (const id of ids) {
        try {
          await window.ABSApi.del("/vouchers/" + id +
            (companyId ? "?company_id=" + companyId : ""));
          vouchers = vouchers.filter((x) => x.id !== id);
        } catch (ex) {
          failed.push(ex.message);
        }
      }
      render();
      if (failed.length) {
        window.ABSToast(failed.length + " could not be removed: " + failed[0], true);
      } else {
        window.ABSToast("Cleared from history. Tally untouched.");
      }
    }
  };
})();
