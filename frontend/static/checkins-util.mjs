// Pure helpers for the check-ins view: client-side validation that mirrors the
// server contract, status chips, schedule summary. No DOM access (node-testable).

export const ANSWER_TYPES = ["boolean", "scale", "single_choice"];
export const REPEATS = ["daily", "weekly", "once"];

export function statusChip(status) {
  return `<span class="chip ${Status.normalize(status)}" data-chip="${Status.normalize(status)}">${Status.label(status)}</span>`;
}

export const Status = {
  normalize(status) {
    return ["pending", "answered", "missed"].includes(status) ? status : "pending";
  },
  isValid(status) {
    return ["pending", "answered", "missed"].includes(status);
  },
  label(status) {
    return { pending: "Pending", answered: "Answered", missed: "Missed" }[status] || "Pending";
  },
  css(status) {
    return this.normalize(status);
  },
};

export function scheduleSummary(schedule) {
  if (!schedule) return "—";
  if (schedule.repeat === "once") return `Once ${schedule.scheduled_at}`;
  const times = (schedule.times || []).join(", ") || "—";
  if (schedule.repeat === "weekly") {
    const names = { 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun" };
    const days = (schedule.days || []).map((d) => names[d] || d).join(", ");
    return `${days} · ${times}`;
  }
  return `Daily · ${times}`;
}

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
const ONCE_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/;

export function validateTime(hhmm) {
  return TIME_RE.test(hhmm);
}

export function validateOnce(dt) {
  const m = ONCE_RE.exec(dt || "");
  if (!m) return false;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  const hour = Number(m[4]);
  const minute = Number(m[5]);
  if (month < 1 || month > 12 || hour > 23 || minute > 59) return false;
  const probe = new Date(Date.UTC(year, month - 1, day, hour, minute));
  return (
    probe.getUTCFullYear() === year &&
    probe.getUTCMonth() + 1 === month &&
    probe.getUTCDate() === day
  );
}

// mirror of the server's CheckinWrite validation (spec/data-and-api.md)
export function validateCheckinForm(form) {
  const errors = {};
  if (!form.title || !form.title.trim()) errors.title = "A question is required.";
  if (!ANSWER_TYPES.includes(form.answer_type)) errors.answer_type = "Invalid answer type.";
  if (form.answer_type === "scale" && form.scale_min >= form.scale_max) {
    errors.scale = "Min must be less than max.";
  }
  if (form.answer_type === "single_choice") {
    const opts = (form.options || []).map((o) => o.trim()).filter(Boolean);
    if (opts.length < 2) errors.options = "At least two non-empty options are required.";
    form.options = opts;
  }
  if (form.repeat !== "once") {
    const times = (form.times || []).filter((t) => t);
    if (times.length === 0) errors.times = "At least one time (HH:MM) is required.";
    else if (times.some((t) => !validateTime(t))) errors.times = "Times must be HH:MM.";
    else form.times = [...new Set(times)].sort();
    if (form.repeat === "weekly") {
      if (!form.days || form.days.length === 0) errors.days = "Pick at least one weekday.";
    }
  } else {
    if (!form.scheduled_at || !validateOnce(form.scheduled_at)) {
      errors.scheduled_at = "Use YYYY-MM-DDTHH:MM.";
    }
  }
  if (!(form.due_window >= 5 && form.due_window <= 1440)) errors.due_window = "5–1440 minutes.";
  if (form.reminder !== 0 && !(form.reminder >= 10 && form.reminder <= 1440)) {
    errors.reminder = "0 (off) or 10–1440 minutes.";
  }
  return { errors, form };
}

export function buildSchedulePayload(form) {
  if (form.repeat === "once") {
    return { repeat: "once", scheduled_at: form.scheduled_at };
  }
  const payload = { repeat: form.repeat, times: [...new Set((form.times || []).filter(Boolean))].sort() };
  if (form.repeat === "weekly") payload.days = [...(form.days || [])].sort((a, b) => a - b);
  return payload;
}

export function definitionToForm(def) {
  const s = def.schedule || {};
  return {
    id: def.id,
    title: def.title,
    answer_type: def.answer_type,
    scale_min: def.scale_min,
    scale_max: def.scale_max,
    options: def.options ? [...def.options] : [],
    repeat: s.repeat || "daily",
    times: s.times ? [...s.times] : [""],
    days: s.days ? [...s.days] : [1, 2, 3, 4, 5],
    scheduled_at: s.scheduled_at || "",
    due_window: def.due_window_minutes,
    reminder: def.reminder_minutes,
  };
}