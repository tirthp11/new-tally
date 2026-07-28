/* Login, token storage, and the dashboard view. */
(function () {
  "use strict";

  const KEY_ACCESS = "abs_access_token";
  const KEY_REFRESH = "abs_refresh_token";
  const KEY_ROLE = "abs_role";
  const KEY_EMAIL = "abs_email";

  window.ABSAuth = {
    isLoggedIn: () => !!localStorage.getItem(KEY_ACCESS),
    role: () => localStorage.getItem(KEY_ROLE) || "operator",
    email: () => localStorage.getItem(KEY_EMAIL) || "",

    async login(email, password) {
      const data = await window.ABSApi.post("/auth/login", { email, password });
      localStorage.setItem(KEY_ACCESS, data.access_token);
      localStorage.setItem(KEY_REFRESH, data.refresh_token);
      localStorage.setItem(KEY_ROLE, data.role);
      localStorage.setItem(KEY_EMAIL, email);
    },

    /* Refresh the authoritative role/email from the server so a stale cached
       value can never reveal admin-only UI. */
    async syncRole() {
      const me = await window.ABSApi.get("/auth/me");
      localStorage.setItem(KEY_ROLE, me.role);
      localStorage.setItem(KEY_EMAIL, me.email);
      return me.role;
    },

    clear() {
      localStorage.removeItem(KEY_ACCESS);
      localStorage.removeItem(KEY_REFRESH);
      localStorage.removeItem(KEY_ROLE);
      localStorage.removeItem(KEY_EMAIL);
    },

    logout() {
      this.clear();
      window.ABSRouter.go("/login");
    },
  };

  // View: login --------------------------------------------------------------
  window.ABSViews = window.ABSViews || {};
  window.ABSViews.login = function () {
    const form = document.getElementById("login-form");
    const err = document.getElementById("login-error");
    window.ABSPwToggle(document.getElementById("login-pw-toggle"), form.password);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.textContent = "";
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      const email = form.email.value.trim();
      const password = form.password.value;
      try {
        await window.ABSAuth.login(email, password);
        window.ABSRouter.go("/");
      } catch (ex) {
        err.textContent = ex.message;
      } finally {
        btn.disabled = false;
      }
    });
  };

  // View: dashboard ------------------------------------------------------------
  window.ABSViews.dashboard = async function () {
    const tbody = document.getElementById("dash-recent");
    document.getElementById("dash-upload").addEventListener("click",
      () => window.ABSUpload.openModal());

    function onCompanyChanged() {
      if (!document.body.contains(tbody)) {
        document.removeEventListener("abs:company-changed", onCompanyChanged);
        return;
      }
      loadDash();
    }
    document.addEventListener("abs:company-changed", onCompanyChanged);

    await window.ABSShell.refreshCompanies();
    await loadDash();

    async function loadDash() {
      if (!document.body.contains(tbody)) return;
      const company = window.ABSShell.company();
      const companyEl = document.getElementById("dash-company");
      if (companyEl) {
        companyEl.textContent = company
          ? company.tally_name + (company.education_mode ? " (education mode on)" : "")
          : "No company selected yet. Pick one from the dropdown at the top right.";
      }

      const statIds = ["stat-drafts", "stat-pushed-month", "stat-pushed-total", "stat-failed"];
      const companyId = window.ABSShell.companyId();
      try {
        const status = await window.ABSApi.get("/connector/status").catch(() => null);
        // The awaits below let the user leave the dashboard mid-load; if they
        // have, these elements are gone and writing .textContent would throw
        // "Cannot set properties of null". Bail instead.
        if (!document.body.contains(tbody)) return;
        const connEl = document.getElementById("dash-connector");
        if (status && connEl) {
          connEl.textContent = status.online
            ? "Desktop connector is online."
            : (status.total === 0
              ? "No desktop connector paired yet. Use \"Pairing code\" in the sidebar."
              : "Desktop connector is offline. Start it on the Tally computer.");
        }

        if (!companyId) {
          statIds.forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.textContent = "-";
          });
          tbody.innerHTML = '<tr><td colspan="5"><div class="empty">' +
            '<div class="empty-title">No company selected</div>' +
            "Select a company from the top-right to see its activity.</div></td></tr>";
          return;
        }

        // Counts come from /vouchers/stats (exact SQL COUNTs); the voucher list
        // is only for the Recent activity table and is capped at 500 rows, so it
        // must not be used for totals.
        const [stats, vouchers] = await Promise.all([
          window.ABSApi.get("/vouchers/stats?company_id=" + companyId),
          window.ABSApi.get("/vouchers?company_id=" + companyId),
        ]);
        if (!document.body.contains(tbody)) return;  // navigated away mid-load
        const setText = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
        setText("stat-drafts", stats.open);
        setText("stat-failed", stats.failed);
        setText("stat-pushed-month", stats.pushed_month);
        setText("stat-pushed-total", stats.pushed_total);
        // Send the month tile to History filtered to the same month the count
        // used. History filters year and month separately, so split "2026-07".
        const monthLink = document.getElementById("stat-month-link");
        if (monthLink && stats.month) {
          const ym = stats.month.split("-");
          monthLink.setAttribute("href", "/history?year=" + ym[0] + "&month=" + ym[1]);
        }

        tbody.innerHTML = "";
        vouchers.slice(0, 8).forEach((v) => {
          const tr = document.createElement("tr");
          tr.innerHTML =
            '<td><span class="badge ' + v.status + '">' + v.status + "</span></td>" +
            "<td>" + escapeHtml(v.party_name) + "</td>" +
            "<td>" + escapeHtml(v.invoice_number) + "</td>" +
            "<td>" + escapeHtml(window.ABSFormat.date(v.invoice_date)) + "</td>" +
            '<td class="right">' + window.ABSFormat.money(v.amount) + "</td>";
          tbody.appendChild(tr);
        });
        if (!vouchers.length) {
          tbody.innerHTML = '<tr><td colspan="5"><div class="empty">' +
            '<div class="empty-title">Nothing here yet</div>' +
            "Upload an invoice from the top bar to get started.</div></td></tr>";
        }
      } catch (ex) {
        window.ABSToast(ex.message, true);
      }
    }
  };
})();
