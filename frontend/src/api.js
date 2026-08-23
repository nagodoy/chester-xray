const SESSION_TOKEN_KEY = "chester.session-token";

export const getSessionToken = () => sessionStorage.getItem(SESSION_TOKEN_KEY);
export const clearSessionToken = () => sessionStorage.removeItem(SESSION_TOKEN_KEY);

const request = async (path, options = {}) => {
  const headers = new Headers(options.headers || {});
  const token = getSessionToken();
  if (token && path.startsWith("/api/")) headers.set("X-Session-Token", token);
  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  if (!response.ok) {
    const message = await response.text();
    const error = new Error(message || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
};

export const listStudies = ({ search = "", status = "", limit = 40, offset = 0 } = {}) =>
  request(`/api/studies?${new URLSearchParams({ ...(search && { search }), ...(status && { status }), limit, offset })}`);
export const getStudy = (id) => request(`/api/studies/${encodeURIComponent(id)}`);
export const uploadStudies = (files) => {
  const body = new FormData();
  [...files].forEach((file) => body.append("files", file));
  body.append("confirm_deidentified", "true");
  return request("/api/uploads", { method: "POST", body });
};
export const retryStudy = (id) => request(`/api/studies/${encodeURIComponent(id)}/retry`, { method: "POST" });
export const reviewStudy = (id, decision) =>
  request(`/api/studies/${encodeURIComponent(id)}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) });
export const getDicomwebSettings = () => request("/api/settings/dicomweb");

export const requestAccessCode = (email) =>
  request("/api/auth/request-code", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email }) });
export const verifyAccessCode = async (email, code) => {
  const result = await request("/api/auth/verify-code", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, code }) });
  sessionStorage.setItem(SESSION_TOKEN_KEY, result.session_token);
  return result.access;
};
export const validateSession = () => request("/api/auth/validate-session");
export const logout = async () => {
  try { await request("/api/auth/logout", { method: "POST" }); } finally { clearSessionToken(); }
};
export const getAccessMetadata = () => request("/api/access-control/metadata");
export const listAllowedEmails = () => request("/api/allowed-emails");
export const createAllowedEmail = (body) => request("/api/allowed-emails", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const updateAllowedEmail = (id, body) => request(`/api/allowed-emails/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const deleteAllowedEmail = (id) => request(`/api/allowed-emails/${encodeURIComponent(id)}`, { method: "DELETE" });
export const listAllowedDomains = () => request("/api/allowed-domains");
export const createAllowedDomain = (body) => request("/api/allowed-domains", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const updateAllowedDomain = (id, body) => request(`/api/allowed-domains/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const deleteAllowedDomain = (id) => request(`/api/allowed-domains/${encodeURIComponent(id)}`, { method: "DELETE" });
export const listAccessAudit = () => request("/api/access-control-audit");
export const listLegacyStudyOwners = () => request("/api/legacy-study-owners");
export const migrateLegacyStudyOwner = (body) => request("/api/legacy-study-owners/migrate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });