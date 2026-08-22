const request = async (path, options = {}) => {
  const response = await fetch(path, { credentials: "include", ...options });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed (${response.status})`);
  }
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