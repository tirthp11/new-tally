/* Tiny vanilla-JS router. Intercepts nav clicks, updates the URL with the
   History API, fetches the matching fragment from /views, swaps it into
   <main>, and runs that view's script. Moving between screens never reloads
   the whole site. The login route renders with no chrome at all. */
(function () {
  "use strict";

  // Cache-buster for the view fragments fetched below. Bump this whenever a
  // /views/*.html file changes so browsers pull the new markup instead of a
  // stale cached copy (e.g. the admin users table gaining a Name column).
  const APP_VERSION = "20260722-push-buffer";

  // path pattern -> { view fragment, init function name, auth required }
  const ROUTES = [
    { pattern: /^\/login$/, view: "login", init: "login", auth: false, title: "Sign in" },
    { pattern: /^\/$/, view: "dashboard", init: "dashboard", auth: true, title: "Dashboard" },
    { pattern: /^\/vouchers$/, view: "vouchers", init: "vouchers", auth: true, title: "Vouchers" },
    { pattern: /^\/history$/, view: "history", init: "history", auth: true, title: "History" },
    { pattern: /^\/admin$/, view: "admin", init: "admin", auth: true, admin: true, title: "Admin" },
    // Old deep links: /review/:id now opens the voucher grid on that row.
    { pattern: /^\/review\/([0-9a-f-]+)$/, redirect: (id) => "/vouchers?open=" + id },
  ];

  const main = document.getElementById("main");

  async function render(pathWithQuery) {
    const path = pathWithQuery.split("?")[0];
    let route = ROUTES.find((r) => r.pattern.test(path));
    if (!route) route = ROUTES[1]; // dashboard

    if (route.redirect) {
      const m = route.pattern.exec(path);
      history.replaceState({}, "", route.redirect(m && m[1]));
      return render(location.pathname + location.search);
    }

    if (route.auth && !window.ABSAuth.isLoggedIn()) {
      history.replaceState({}, "", "/login");
      route = ROUTES[0];
    }
    // Pull the authoritative role from the server once per session before any
    // role-gated chrome is rendered, so a stale cached role can't leak admin UI.
    if (window.ABSAuth.isLoggedIn() && !render._roleSynced) {
      render._roleSynced = true;
      try { await window.ABSAuth.syncRole(); } catch (ex) { /* keep cached role */ }
    }
    if (route.admin && window.ABSAuth.role() !== "admin") {
      // Silent redirect: operators never have an Admin link, and the API
      // enforces admin on every /admin route anyway.
      route = ROUTES[1];
      history.replaceState({}, "", "/");
    }

    refreshChrome(route);

    const res = await fetch("/views/" + route.view + ".html?v=" + APP_VERSION);
    main.innerHTML = await res.text();

    document.querySelectorAll(".sidebar-nav a").forEach((a) => {
      const href = a.getAttribute("href");
      a.classList.toggle("active", href === path || (href !== "/" && path.startsWith(href)));
    });

    const init = window.ABSViews && window.ABSViews[route.init];
    if (typeof init === "function") {
      try { await init(); } catch (ex) { window.ABSToast(ex.message, true); }
    }
  }

  function go(path) {
    history.pushState({}, "", path);
    render(path);
    document.body.classList.remove("sidebar-open");
  }

  // Admin-only nav nodes are captured once (with where to re-insert them) so
  // they can be fully removed for operators and restored for admins.
  let adminNodes = null;
  function applyAdminNav(isAdmin) {
    if (!adminNodes) {
      adminNodes = Array.from(document.querySelectorAll("[data-admin-only]"))
        .map((el) => ({ el, parent: el.parentNode, next: el.nextSibling }));
    }
    adminNodes.forEach(({ el, parent, next }) => {
      if (isAdmin) {
        if (!el.isConnected) parent.insertBefore(el, next && next.isConnected ? next : null);
        el.hidden = false;
      } else if (el.isConnected) {
        el.remove();
      }
    });
  }

  function refreshChrome(route) {
    const loggedIn = window.ABSAuth.isLoggedIn();
    const showChrome = loggedIn && !(route && route.view === "login");
    document.body.classList.toggle("no-chrome", !showChrome);
    if (showChrome) {
      document.getElementById("user-email").textContent = window.ABSAuth.email();
      // Role is authoritative here (synced from the server above). For a
      // non-admin the admin chrome is removed from the DOM entirely; for an
      // admin it is restored if a previous operator session removed it.
      applyAdminNav(window.ABSAuth.role() === "admin");
      if (route && route.title) document.getElementById("page-title").textContent = route.title;
      if (!refreshChrome._started) {
        refreshChrome._started = true;
        window.ABSShell.onLogin();
      }
    } else {
      refreshChrome._started = false;
      render._roleSynced = false;  // re-sync role on the next login
    }
  }

  document.addEventListener("click", (e) => {
    const link = e.target.closest("a[data-link]");
    if (link) {
      e.preventDefault();
      go(link.getAttribute("href"));
    }
  });

  window.addEventListener("popstate", () => render(location.pathname + location.search));

  window.ABSRouter = { go, refreshChrome };

  document.addEventListener("DOMContentLoaded", () =>
    render(location.pathname + location.search));
})();
