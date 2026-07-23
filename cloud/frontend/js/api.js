/* fetch() wrapper: attaches the JWT, talks JSON, handles auth failures. */
(function () {
  "use strict";

  const BASE = "/api/v1";

  function token() {
    return localStorage.getItem("abs_access_token") || "";
  }

  async function request(method, path, body, isForm) {
    const headers = {};
    const t = token();
    if (t) headers["Authorization"] = "Bearer " + t;
    let payload;
    if (isForm) {
      payload = body; // FormData: browser sets the content type
    } else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const res = await fetch(BASE + path, { method, headers, body: payload });
    if (res.status === 401 && path !== "/auth/login") {
      window.ABSAuth.clear();
      window.ABSRouter.go("/login");
      throw new Error("Session expired. Please log in again.");
    }
    let data = null;
    const text = await res.text();
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = { detail: text }; }
    }
    if (!res.ok) {
      const msg = data && (data.detail || data.error) ? (data.detail || data.error) : ("Request failed (" + res.status + ")");
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  }

  window.ABSApi = {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
    put: (path, body) => request("PUT", path, body),
    del: (path) => request("DELETE", path),
    upload: (path, formData) => request("POST", path, formData, true),
  };

  window.ABSToast = function (message, isError) {
    const el = document.getElementById("toast");
    el.textContent = message;
    el.classList.toggle("error", !!isError);
    el.hidden = false;
    clearTimeout(el._timer);
    el._timer = setTimeout(() => { el.hidden = true; }, isError ? 6000 : 3500);
  };
})();
