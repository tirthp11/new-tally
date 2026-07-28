/* Admin panel: user management only (docs/09 section 2.3).
   Create and delete users; the server refuses deleting yourself or the
   last remaining admin. */
(function () {
  "use strict";
  window.ABSViews = window.ABSViews || {};

  window.ABSViews.admin = async function () {
    document.getElementById("adm-user-create").addEventListener("click", createUser);
    window.ABSPwToggle(
      document.getElementById("adm-user-pw-toggle"),
      document.getElementById("adm-user-password"));
    await loadUsers();

    async function createUser() {
      const nameEl = document.getElementById("adm-user-name");
      const name = nameEl ? nameEl.value.trim() : "";
      const email = document.getElementById("adm-user-email").value.trim();
      const password = document.getElementById("adm-user-password").value;
      const role = document.getElementById("adm-user-role").value;
      if (!email || !password) {
        window.ABSToast("Email and password are required.", true);
        return;
      }
      const btn = document.getElementById("adm-user-create");
      btn.disabled = true;
      try {
        await window.ABSApi.post("/admin/users", { name, email, password, role });
        window.ABSToast("User created.", false, true);
        if (nameEl) nameEl.value = "";
        document.getElementById("adm-user-email").value = "";
        document.getElementById("adm-user-password").value = "";
        await loadUsers();
      } catch (ex) {
        window.ABSToast(ex.message, true);
      } finally {
        btn.disabled = false;
      }
    }

    async function loadUsers() {
      const users = await window.ABSApi.get("/admin/users");
      const tbody = document.getElementById("adm-users");
      if (!tbody) return;
      tbody.innerHTML = "";
      users.forEach((u) => {
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + (u.name ? escapeHtml(u.name) : '<span class="muted">-</span>') + "</td>" +
          "<td>" + escapeHtml(u.email) + "</td>" +
          "<td>" + escapeHtml(u.role) + "</td>" +
          '<td class="pw-cell"></td>' +
          "<td>" + (u.created_at ? new Date(u.created_at).toLocaleDateString() : "-") + "</td>" +
          "<td></td>";
        buildPwCell(u, tr.querySelector(".pw-cell"));
        const edit = document.createElement("button");
        edit.className = "btn btn-sm";
        edit.textContent = "Edit";
        edit.addEventListener("click", () => editUser(u));
        tr.lastElementChild.appendChild(edit);
        tr.lastElementChild.appendChild(document.createTextNode(" "));

        const del = document.createElement("button");
        del.className = "btn btn-danger btn-sm";
        del.textContent = "Delete";
        del.addEventListener("click", async () => {
          const ok = await window.ABSModal.confirm({
            title: "Delete user",
            body: "Delete " + u.email + "? They will no longer be able to sign in. " +
              "Their uploads and history stay in the system.",
            okText: "Delete", danger: true,
          });
          if (!ok) return;
          try {
            await window.ABSApi.del("/admin/users/" + u.id);
            window.ABSToast("User deleted.");
            await loadUsers();
          } catch (ex) {
            window.ABSToast(ex.message, true);
          }
        });
        tr.lastElementChild.appendChild(del);
        tbody.appendChild(tr);
      });
    }

    // Password column (docs/13, A1): stored passwords are hashed and cannot be
    // shown. The cell masks the value and offers an inline Reset with a
    // show/hide toggle so the admin can see the NEW password while typing.
    function buildPwCell(u, cell) {
      cell.innerHTML = "";
      const mask = document.createElement("span");
      mask.className = "pw-mask";
      mask.textContent = "••••••••";
      const reset = document.createElement("button");
      reset.className = "btn btn-sm";
      reset.textContent = "Reset";
      reset.addEventListener("click", () => openReset());
      cell.appendChild(mask);
      cell.appendChild(document.createTextNode(" "));
      cell.appendChild(reset);

      function openReset() {
        cell.innerHTML = "";
        const wrap = document.createElement("div");
        wrap.className = "pw-wrap";
        const input = document.createElement("input");
        input.type = "text";  // visible by default so the admin can read it
        input.placeholder = "New password";
        input.style.minWidth = "150px";
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "pw-toggle";
        toggle.textContent = "Hide";
        toggle.setAttribute("aria-label", "Hide password");
        window.ABSPwToggle(toggle, input);
        wrap.appendChild(input);
        wrap.appendChild(toggle);

        const save = document.createElement("button");
        save.className = "btn btn-primary btn-sm";
        save.textContent = "Save";
        const cancel = document.createElement("button");
        cancel.className = "btn btn-sm";
        cancel.textContent = "Cancel";
        cancel.addEventListener("click", () => buildPwCell(u, cell));
        save.addEventListener("click", async () => {
          const pw = input.value;
          if (!pw) { window.ABSToast("Enter a new password.", true); return; }
          save.disabled = true;
          try {
            await window.ABSApi.put("/admin/users/" + u.id, { password: pw });
            window.ABSToast("Password reset for " + u.email + ".", false, true);
            buildPwCell(u, cell);
          } catch (ex) {
            window.ABSToast(ex.message, true);
            save.disabled = false;
          }
        });

        cell.appendChild(wrap);
        cell.appendChild(document.createTextNode(" "));
        cell.appendChild(save);
        cell.appendChild(document.createTextNode(" "));
        cell.appendChild(cancel);
        input.focus();
      }
    }

    function editUser(u) {
      const modal = window.ABSModal.open({
        title: "Edit user",
        bodyHtml:
          '<div class="field"><label>Name</label>' +
          '<input id="em-name" type="text" placeholder="Optional" value="' + escapeHtml(u.name || "") + '"></div>' +
          '<div class="field"><label>Email</label>' +
          '<input id="em-email" type="email" value="' + escapeHtml(u.email) + '"></div>' +
          '<div class="field"><label>New password (leave blank to keep current)</label>' +
          '<div class="pw-wrap">' +
          '<input id="em-pw" type="password" placeholder="Unchanged" autocomplete="new-password">' +
          '<button type="button" class="pw-toggle" id="em-pw-toggle" aria-label="Show password">Show</button>' +
          "</div></div>" +
          '<div class="field"><label>Role</label><select id="em-role">' +
          '<option value="operator"' + (u.role === "operator" ? " selected" : "") + ">Operator</option>" +
          '<option value="admin"' + (u.role === "admin" ? " selected" : "") + ">Admin</option>" +
          "</select></div>" +
          '<div class="field"><label><input id="em-active" type="checkbox"' +
          (u.is_active ? " checked" : "") + ' style="width:auto"> Active (can sign in)</label></div>' +
          '<p class="muted" style="margin-bottom:0">Existing passwords are stored encrypted and ' +
          "cannot be shown. Set a new one here to reset it.</p>",
        footHtml:
          '<button class="btn" data-act="cancel">Cancel</button>' +
          '<button class="btn btn-primary" data-act="save">Save changes</button>',
      });
      const body = modal.body;
      window.ABSPwToggle(body.querySelector("#em-pw-toggle"), body.querySelector("#em-pw"));
      modal.foot.querySelector('[data-act="cancel"]').addEventListener("click", () => modal.close());
      modal.foot.querySelector('[data-act="save"]').addEventListener("click", async () => {
        const payload = {};
        const name = body.querySelector("#em-name").value.trim();
        const email = body.querySelector("#em-email").value.trim();
        const pw = body.querySelector("#em-pw").value;
        const role = body.querySelector("#em-role").value;
        const active = body.querySelector("#em-active").checked;
        if (!email) { window.ABSToast("Email is required.", true); return; }
        if (name !== (u.name || "")) payload.name = name;
        if (email.toLowerCase() !== u.email) payload.email = email;
        if (pw) payload.password = pw;
        if (role !== u.role) payload.role = role;
        if (active !== u.is_active) payload.is_active = active;
        if (!Object.keys(payload).length) { modal.close(); return; }
        const saveBtn = modal.foot.querySelector('[data-act="save"]');
        saveBtn.disabled = true;
        try {
          await window.ABSApi.put("/admin/users/" + u.id, payload);
          window.ABSToast("User updated.", false, true);
          modal.close();
          await loadUsers();
        } catch (ex) {
          window.ABSToast(ex.message, true);
          saveBtn.disabled = false;
        }
      });
    }
  };
})();
