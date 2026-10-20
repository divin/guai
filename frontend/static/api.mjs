// Typed fetch wrapper for the same-origin /api/* endpoints (spec/frontend.md).
// Pure module: no DOM access so it can be imported by node:test.

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
  }
}

export function buildQuery(params = {}) {
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") usp.set(key, value);
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

function pickDetail(payload) {
  if (!payload) return "";
  const d = payload.detail;
  if (Array.isArray(d)) {
    return d.map((item) => item.msg || "").join("; ");
  }
  return typeof d === "string" ? d : "";
}

export async function request(path, { method = "GET", body, params } = {}) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(`${path}${buildQuery(params)}`, init);
  if (!resp.ok) {
    let detail = "";
    try {
      detail = pickDetail(await resp.json());
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return null;
  const ct = resp.headers.get("content-type") || "";
  return ct.includes("application/json") ? resp.json() : resp.text();
}

export const getJSON = (path, params) => request(path, { params });
export const postJSON = (path, body) => request(path, { method: "POST", body });
export const putJSON = (path, body) => request(path, { method: "PUT", body });
export const del = (path) => request(path, { method: "DELETE" });

const FRIENDLY = {
  401: "Not authorised — check the server setup.",
  404: "Not found.",
  409: "Conflict: this check-in already has scheduled instances; deactivate it instead.",
  410: "Too late: the answer window has closed.",
  502: "Weather/geocoding service unavailable.",
};

export function friendlyError(err) {
  if (err && err.name === "ApiError") {
    if (err.status === 422 && err.message) return err.message;
    return FRIENDLY[err.status] || `Request failed (HTTP ${err.status}).`;
  }
  return "Network error — is the server running?";
}

export function shortDate(iso) {
  return iso.slice(0, 16).replace("T", " ");
}