/* Upload modal: dropzone -> one status row per file (upload / AI extraction /
   draft) -> the voucher grid. Multiple files extract in parallel through a
   bounded pool, so uploading a stack of invoices is fast without hammering the
   AI. Files are processed in memory on the server and never stored. */
(function () {
  "use strict";

  // How many files extract at once. Each POST /documents runs the AI vision
  // call server-side (a slow, network-bound step); FastAPI serves the sync
  // endpoint from its threadpool, so these overlap for real. Kept at 4 to match
  // the push flow (MAX_CONCURRENT_PUSH) and stay clear of OpenAI rate limits.
  const MAX_CONCURRENT_UPLOAD = 4;

  function openModal() {
    if (!window.ABSShell.companyId()) {
      window.ABSToast("Select a company first (top right), so the voucher lands in the right place.", true);
      return;
    }

    const h = window.ABSModal.open({
      title: "Upload invoices",
      blockOverlayClose: true,
      bodyHtml:
        '<div id="up-zone" class="dropzone">' +
        "<strong>Drop PDFs or images here</strong><br>or click to choose files<br>" +
        '<span style="font-size:13px">PDF, JPG or PNG. Multiple files extract in parallel. Read in memory, never stored.</span>' +
        "</div>" +
        '<input type="file" id="up-file" accept=".pdf,.png,.jpg,.jpeg" multiple hidden>' +
        '<div id="up-progress" hidden>' +
        '<ul class="upload-stages" id="up-list"></ul>' +
        '<div id="up-summary" class="mt-sm" hidden></div>' +
        '<button class="btn mt-sm" id="up-done" hidden></button>' +
        "</div>",
    });

    const zone = h.body.querySelector("#up-zone");
    const fileInput = h.body.querySelector("#up-file");
    const progressWrap = h.body.querySelector("#up-progress");
    const list = h.body.querySelector("#up-list");
    const summary = h.body.querySelector("#up-summary");
    // Shown only once a batch has finished. This modal takes one batch and then
    // closes: no "upload more" while files are still extracting, which read as
    // an invitation to pile more on mid-run. To send more, reopen it.
    const doneBtn = h.body.querySelector("#up-done");

    zone.addEventListener("click", () => fileInput.click());
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("dragover"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
      if (e.dataTransfer.files.length) startBatch(Array.from(e.dataTransfer.files));
    });
    fileInput.addEventListener("change", () => {
      if (fileInput.files.length) startBatch(Array.from(fileInput.files));
    });

    // One status row per file: number/spinner icon, filename, status text, and
    // an inline error line when it fails. Returned handle drives that one row.
    function addRow(file, index) {
      const li = document.createElement("li");
      li.innerHTML =
        '<span class="stage-ico">' + (index + 1) + "</span>" +
        '<span class="up-name"></span>' +
        '<span class="up-status"></span>';
      const err = document.createElement("div");
      err.className = "field-error up-err";
      err.hidden = true;
      li.appendChild(err);
      list.appendChild(li);

      const nameEl = li.querySelector(".up-name");
      const statusEl = li.querySelector(".up-status");
      const icoEl = li.querySelector(".stage-ico");
      nameEl.textContent = file.name;

      return {
        setStatus(text, cls) {
          statusEl.textContent = text;
          li.className = cls || "";
          if (cls === "done") icoEl.textContent = "✓";
          else if (cls === "error") icoEl.textContent = "!";
          else icoEl.textContent = String(index + 1);
        },
        fail(message) {
          this.setStatus("Failed", "error");
          err.textContent = message;
          err.hidden = false;
        },
      };
    }

    // Phase 1 for one file: upload + server-side AI extraction. Resolves to the
    // extracted document, or throws so the pool can record the failure on this
    // file's row. Deliberately stops short of creating the draft - see startBatch.
    async function extractOne(file, row) {
      row.setStatus("Uploading...", "active");
      const doc = await uploadWithProgress(file, (pct) => {
        if (pct >= 100) row.setStatus("AI is reading the invoice...", "active");
      });
      if (doc.status !== "extracted") {
        throw new Error(doc.error || "Extraction failed. Try a clearer scan.");
      }
      row.setStatus("Waiting to create draft...", "active");
      return doc;
    }

    async function startBatch(files) {
      zone.hidden = true;
      progressWrap.hidden = false;
      list.innerHTML = "";
      summary.hidden = true;
      doneBtn.hidden = true;

      const rows = files.map((f, i) => addRow(f, i));
      const docs = new Array(files.length).fill(null);  // by SELECTION index
      const drafts = [];  // successful drafts, in selection order
      let failures = 0;
      let expired = false;

      // Phase 1 - extract in parallel. Bounded worker pool: keep up to
      // MAX_CONCURRENT_UPLOAD files in flight and pull the next as each finishes.
      // Each row updates independently, so the parallel extractions show their
      // own progress. Results are stored AT their selection index, never
      // appended, so a fast small file cannot overtake a slow large one.
      let next = 0;
      async function worker() {
        while (next < files.length) {
          const i = next++;
          try {
            docs[i] = await extractOne(files[i], rows[i]);
          } catch (ex) {
            failures++;
            rows[i].fail(ex.message);
            if (ex && ex.sessionExpired) { expired = true; return; }  // router already redirected
          }
        }
      }
      const pool = [];
      for (let i = 0; i < Math.min(MAX_CONCURRENT_UPLOAD, files.length); i++) {
        pool.push(worker());
      }
      await Promise.allSettled(pool);

      // Phase 2 - create the drafts SERIALLY, in selection order. The voucher
      // grid sorts on each draft's created_at, so writing them in this order is
      // what makes file 1, file 2, file 3 appear in the order they were picked.
      // Creating them inside the pool above instead would stamp created_at in
      // extraction-completion order and scramble the sequence. Nothing slow
      // happens here (no AI call - the masters are already cached), so
      // serialising costs milliseconds per file.
      for (let i = 0; i < files.length && !expired; i++) {
        if (!docs[i]) continue;  // this file failed extraction; its row already says so
        rows[i].setStatus("Creating voucher draft...", "active");
        try {
          drafts.push(await window.ABSApi.post(
            "/vouchers?company_id=" + window.ABSShell.companyId(), { document_id: docs[i].id }));
          rows[i].setStatus("Ready", "done");
        } catch (ex) {
          failures++;
          rows[i].fail(ex.message);
          // ABSApi throws a plain Error on 401 but clears the token first, so a
          // logged-out state is the reliable signal to stop the rest of the batch.
          if (!window.ABSAuth.isLoggedIn()) { expired = true; }
        }
      }

      // One file, success: keep the old behavior - land directly on its row.
      if (files.length === 1 && drafts.length === 1) {
        h.close();
        window.ABSToast("Invoice extracted. Resolve the ledgers and push to Tally.", false, true);
        window.ABSRouter.go("/vouchers?open=" + drafts[0].voucher.id);
        return;
      }

      // Many files, all succeeded: close and land on the voucher list.
      if (drafts.length && failures === 0) {
        h.close();
        window.ABSToast(
          drafts.length + " invoices extracted. Resolve the ledgers and push to Tally.", false, true);
        window.ABSRouter.go("/vouchers");
        return;
      }

      // Some failed (or nothing succeeded): stay open so the per-file errors can
      // be read, with one way out. When drafts were created that exit goes to the
      // grid - otherwise those drafts would be stranded behind this modal.
      summary.hidden = false;
      summary.textContent = drafts.length
        ? drafts.length + " extracted, " + failures + " failed. The extracted ones are saved as drafts; re-upload the failed files from the Upload button."
        : "None could be extracted. Try clearer scans.";
      doneBtn.textContent = drafts.length ? "Go to vouchers" : "Close";
      doneBtn.hidden = false;
      doneBtn.onclick = () => {
        h.close();
        if (drafts.length) window.ABSRouter.go("/vouchers");
      };
    }
  }

  function uploadWithProgress(file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/v1/documents");
      xhr.setRequestHeader("Authorization",
        "Bearer " + (localStorage.getItem("abs_access_token") || ""));
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      };
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) { /* not json */ }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data);
        } else if (xhr.status === 401) {
          window.ABSAuth.clear();
          window.ABSRouter.go("/login");
          const err = new Error("Session expired. Please log in again.");
          err.sessionExpired = true;
          reject(err);
        } else {
          reject(new Error((data && data.detail) || "Upload failed (" + xhr.status + ")"));
        }
      };
      xhr.onerror = () => reject(new Error("Network error during upload."));
      xhr.send((() => { const f = new FormData(); f.append("file", file); return f; })());
    });
  }

  window.ABSUpload = { openModal };
})();
