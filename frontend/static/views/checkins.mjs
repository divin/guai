// Check-ins view: definition list with counts, editor (mirrors server validation),
// and per-definition instance/response history.

import { del, getJSON, postJSON, putJSON, shortDate, friendlyError } from "../api.mjs";
import {
  scheduleSummary,
  statusChip,
  validateCheckinForm,
  buildSchedulePayload,
  definitionToForm,
} from "../checkins-util.mjs";

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function typeBadge(type) {
  return `<span class="chip pending">${escapeHtml(type)}</span>`;
}

export const Checkins = {
  async mount(app) {
    let defs = [];
    try {
      defs = await getJSON("/api/checkins");
    } catch (err) {
      app.innerHTML = `<div class="error-banner" role="alert">${friendlyError(err)}</div>`;
      return;
    }

    const items = defs
      .map((d) => {
        const c = d.instance_counts || {};
        return `
        <div class="card">
          <div class="row">
            <h3 style="margin:0;flex:1">${escapeHtml(d.title)}</h3>
            ${typeBadge(d.answer_type)}
            ${d.active ? "" : `<span class="chip missed">inactive</span>`}
          </div>
          <p class="muted" style="margin:0.4rem 0">${scheduleSummary(d.schedule)} · window ${d.due_window_minutes}m ${d.reminder_minutes ? `· reminder ${d.reminder_minutes}m` : ""}</p>
          <p class="row" style="margin:0.4rem 0">
            <span class="chip pending">${c.pending || 0} pending</span>
            <span class="chip answered">${c.answered || 0} answered</span>
            <span class="chip missed">${c.missed || 0} missed</span>
          </p>
          <div class="row" style="margin-top:0.6rem">
            <button class="btn" data-act="detail" data-id="${d.id}">History</button>
            <button class="btn" data-act="edit" data-id="${d.id}">Edit</button>
            ${d.active
              ? `<button class="btn" data-act="deactivate" data-id="${d.id}">Deactivate</button>`
              : `<button class="btn" data-act="activate" data-id="${d.id}">Activate</button>`}
            <button class="btn danger" data-act="delete" data-id="${d.id}" ${c.pending + c.answered + c.missed ? "disabled title=\"has history\"" : ""}>Delete</button>
          </div>
          <div data-history="${d.id}" hidden></div>
        </div>`;
      })
      .join("");

    app.innerHTML = `
      <div class="row" style="margin-bottom:0.8rem">
        <h2 class="view-title" style="margin:0;flex:1">Check-ins</h2>
        <button class="btn primary" id="new-checkin">+ New check-in</button>
      </div>
      <div id="def-list" class="grid">${items || `<div class="empty">No check-ins yet — define your first question.</div>`}</div>
      <div id="editor-host"></div>
      <div class="error-banner" id="checkin-error" role="alert" hidden></div>`;

    app.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => this.onAction(btn.dataset.act, btn.dataset.id, app));
    });
    app.querySelector("#new-checkin").addEventListener("click", () => {
      this.renderEditor(app, null, defs);
    });
  },

  async onAction(act, id, app) {
    const errBox = app.querySelector("#checkin-error");
    let reload = false;
    try {
      if (act === "edit") {
        const def = await getJSON(`/api/checkins/${id}`);
        this.renderEditor(app, def);
        return;
      }
      if (act === "detail") {
        const host = app.querySelector(`[data-history="${id}"]`);
        host.hidden = !host.hidden;
        if (!host.hidden && !host.dataset.loaded) await this.renderHistory(host, id);
        return;
      }
      if (act === "deactivate") await postJSON(`/api/checkins/${id}/deactivate`);
      else if (act === "activate") await postJSON(`/api/checkins/${id}/activate`);
      else if (act === "delete") await del(`/api/checkins/${id}`);
      reload = true;
    } catch (err) {
      errBox.textContent = friendlyError(err);
      errBox.hidden = false;
    }
    if (reload) this.mount(app);
  },

  async renderHistory(host, id) {
    host.dataset.loaded = "1";
    const [instances, responses] = await Promise.all([
      getJSON("/api/checkin-instances", { definition_id: id }),
      getJSON("/api/checkin-responses", { definition_id: id }),
    ]);
    host.innerHTML = `
      <hr style="border-color:var(--border);margin:0.8rem 0">
      <h4>Instances</h4>
      ${
        instances.length
          ? `<div class="table-scroll"><table><thead><tr><th>Scheduled (local)</th><th>Status</th><th>Answered</th><th>Missed</th></tr></thead>
             <tbody>${instances
               .map((i) => `<tr><td>${i.scheduled_local}</td><td>${statusChip(i.status)}</td><td>${i.answered_at || "—"}</td><td>${i.missed_at || "—"}</td></tr>`)
               .join("")}</tbody></table></div>`
          : `<p class="muted">No instances yet.</p>`
      }
      <h4 style="margin-top:0.8rem">Responses</h4>
      ${
        responses.length
          ? `<div class="table-scroll"><table><thead><tr><th>Submitted (UTC)</th><th>Scheduled</th><th>Value</th></tr></thead>
             <tbody>${responses
               .map((r) => `<tr><td>${shortDate(r.submitted_at)}</td><td>${r.scheduled_local}</td><td>${escapeHtml(JSON.stringify(r.value))}</td></tr>`)
               .join("")}</tbody></table></div>`
          : `<p class="muted">No responses yet.</p>`
      }`;
  },

  renderEditor(app, def, defs) {
    const form = definitionToForm(def || {
      title: "",
      answer_type: "boolean",
      scale_min: 0,
      scale_max: 10,
      options: [],
      schedule: { repeat: "daily", times: ["09:00"] },
      due_window_minutes: 60,
      reminder_minutes: 0,
    });
    const host = app.querySelector("#editor-host");
    host.innerHTML = `
      <div class="card">
        <h3>${def ? "Edit check-in" : "New check-in"}</h3>
        <form id="def-form">
          <label class="field"><span class="t">Question text</span><input name="title" value="${escapeHtml(form.title)}" style="width:100%" required></label>
          <div class="row">
            <label class="field">Boolean <input type="radio" name="answer_type" value="boolean" ${form.answer_type === "boolean" ? "checked" : ""}></label>
            <label class="field">Scale <input type="radio" name="answer_type" value="scale" ${form.answer_type === "scale" ? "checked" : ""}></label>
            <label class="field">Single choice <input type="radio" name="answer_type" value="single_choice" ${form.answer_type === "single_choice" ? "checked" : ""}></label>
          </div>
          <div id="scale-fields" ${form.answer_type === "scale" ? "" : "hidden"}>
            <label class="field"><span class="t">Scale min</span><input type="number" name="scale_min" value="${form.scale_min}"></label>
            <label class="field"><span class="t">Scale max</span><input type="number" name="scale_max" value="${form.scale_max}"></label>
          </div>
          <label class="field" id="options-field" ${form.answer_type === "single_choice" ? "" : "hidden"}>
            <span class="t">Options (one per line, at least 2)</span>
            <textarea name="options" rows="3" style="width:100%">${form.options.join("\n")}</textarea>
          </label>
          <div class="row">
            <label class="field">Daily <input type="radio" name="repeat" value="daily" ${form.repeat === "daily" ? "checked" : ""}></label>
            <label class="field">Weekly <input type="radio" name="repeat" value="weekly" ${form.repeat === "weekly" ? "checked" : ""}></label>
            <label class="field">Once <input type="radio" name="repeat" value="once" ${form.repeat === "once" ? "checked" : ""}></label>
          </div>
          <label class="field" id="times-field" ${form.repeat === "once" ? "hidden" : ""}>
            <span class="t">Times (HH:MM, comma or line separated)</span>
            <input name="times" value="${(form.times || []).join(", ")}" style="width:100%">
          </label>
          <div id="days-field" ${form.repeat === "weekly" ? "" : "hidden"}>
            <span class="t">Weekdays</span>
            <div class="row">
              ${[1, 2, 3, 4, 5, 6, 7]
                .map((d) => `<label>${["M", "T", "W", "T", "F", "S", "S"][d - 1]} <input type="checkbox" data-day="${d}" ${(form.days || []).includes(d) ? "checked" : ""}></label>`)
                .join("")}
            </div>
          </div>
          <label class="field" id="once-field" ${form.repeat === "once" ? "" : "hidden"}>
            <span class="t">Date & time (YYYY-MM-DDTHH:MM local)</span>
            <input name="scheduled_at" value="${form.scheduled_at}" style="width:100%">
          </label>
          <div class="row">
            <label class="field"><span class="t">Answer window (minutes, 5–1440)</span><input type="number" name="due_window" value="${form.due_window}" min="5" max="1440"></label>
            <label class="field"><span class="t">Reminder (minutes, 0=off/10–1440)</span><input type="number" name="reminder" value="${form.reminder}" min="0" max="1440"></label>
          </div>
          <div id="form-errors"></div>
          <div class="row" style="margin-top:0.8rem">
            <button class="btn primary" type="submit">Save</button>
            <button class="btn" type="button" id="cancel-edit">Cancel</button>
          </div>
        </form>
      </div>`;

    host.querySelectorAll('input[name="answer_type"]').forEach((r) =>
      r.addEventListener("change", () => {
        const val = host.querySelector('input[name="answer_type"]:checked').value;
        host.querySelector("#scale-fields").hidden = val !== "scale";
        host.querySelector("#options-field").hidden = val !== "single_choice";
      }),
    );
    host.querySelectorAll('input[name="repeat"]').forEach((r) =>
      r.addEventListener("change", () => {
        const val = host.querySelector('input[name="repeat"]:checked').value;
        host.querySelector("#times-field").hidden = val === "once";
        host.querySelector("#days-field").hidden = val !== "weekly";
        host.querySelector("#once-field").hidden = val !== "once";
      }),
    );
    host.querySelector("#cancel-edit").addEventListener("click", () => (host.innerHTML = ""));

    host.querySelector("#def-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const gather = () => ({
        title: host.querySelector('[name="title"]').value,
        answer_type: host.querySelector('input[name="answer_type"]:checked').value,
        scale_min: Number(host.querySelector('[name="scale_min"]').value),
        scale_max: Number(host.querySelector('[name="scale_max"]').value),
        options: host
          .querySelector('[name="options"]')
          .value.split("\n")
          .map((o) => o.trim())
          .filter(Boolean),
        repeat: host.querySelector('input[name="repeat"]:checked').value,
        times: host
          .querySelector('[name="times"]')
          .value.split(/[,\n;]/)
          .map((t) => t.trim()),
        days: [...host.querySelectorAll('[data-day]:checked')].map((c) => Number(c.dataset.day)),
        scheduled_at: host.querySelector('[name="scheduled_at"]').value,
        due_window: Number(host.querySelector('[name="due_window"]').value),
        reminder: Number(host.querySelector('[name="reminder"]').value),
      });

      const { errors, form: validated } = validateCheckinForm(gather());
      const errBox = host.querySelector("#form-errors");
      if (Object.keys(errors).length) {
        errBox.innerHTML = `<div class="error-banner">${Object.values(errors).map(escapeHtml).join("<br>")}</div>`;
        return;
      }
      const payload = {
        title: validated.title.trim(),
        answer_type: validated.answer_type,
        scale_min: validated.scale_min,
        scale_max: validated.scale_max,
        options: validated.answer_type === "single_choice" ? validated.options : null,
        schedule: buildSchedulePayload(validated),
        due_window_minutes: validated.due_window,
        reminder_minutes: validated.reminder,
      };
      try {
        if (def) await putJSON(`/api/checkins/${def.id}`, payload);
        else await postJSON("/api/checkins", payload);
        host.innerHTML = "";
        this.mount(app);
      } catch (err) {
        errBox.innerHTML = `<div class="error-banner">${friendlyError(err)}</div>`;
      }
    });
  },
};