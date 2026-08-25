import type {
  Access,
  AccessMetadata,
  AuditEntry,
  DicomwebSettings,
  ManagedDomain,
  ManagedUser,
  StudyDetail,
  StudyList,
  UploadOutcome,
} from "./types";

const SESSION_TOKEN_KEY = "chester.session-token";

/**
 * The session token lives in sessionStorage and travels in a header, not a cookie.
 * Nothing is sent automatically by the browser, so there is no CSRF surface.
 */
export const getSessionToken = (): string | null => {
  try {
    return sessionStorage.getItem(SESSION_TOKEN_KEY);
  } catch {
    return null;
  }
};

export const setSessionToken = (token: string): void => {
  try {
    sessionStorage.setItem(SESSION_TOKEN_KEY, token);
  } catch {
    /* Private mode and blocked storage both land here; the session is simply
       lost on reload rather than the app failing outright. */
  }
};

export const clearSessionToken = (): void => {
  try {
    sessionStorage.removeItem(SESSION_TOKEN_KEY);
  } catch {
    /* nothing to clear */
  }
};

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Pull the human-readable message out of a FastAPI error body. */
const errorMessage = async (response: Response): Promise<string> => {
  const text = await response.text();
  if (!text) return `Request failed (${response.status})`;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
    if (Array.isArray(parsed.detail)) {
      const first = parsed.detail[0] as { msg?: string } | undefined;
      if (first?.msg) return first.msg;
    }
  } catch {
    /* Not JSON; fall through to the raw text. */
  }
  return text;
};

const request = async <T>(path: string, options: RequestInit = {}): Promise<T> => {
  const headers = new Headers(options.headers);
  const token = getSessionToken();
  if (token) headers.set("X-Session-Token", token);

  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  if (!response.ok) {
    throw new ApiError(await errorMessage(response), response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
};

const json = (body: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  requestCode: (email: string) =>
    request<{ ok: boolean; message: string }>("/api/auth/request-code", {
      method: "POST",
      ...json({ email }),
    }),

  verifyCode: async (email: string, code: string): Promise<Access> => {
    const result = await request<{ session_token: string; access: Access }>(
      "/api/auth/verify-code",
      { method: "POST", ...json({ email, code }) },
    );
    setSessionToken(result.session_token);
    return result.access;
  },

  validateSession: () =>
    request<{ authenticated: boolean; access: Access }>("/api/auth/validate-session"),

  logout: async (): Promise<void> => {
    try {
      await request("/api/auth/logout", { method: "POST" });
    } finally {
      clearSessionToken();
    }
  },

  listStudies: (params: { search?: string; status?: string; limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    if (params.search) query.set("search", params.search);
    if (params.status) query.set("status", params.status);
    query.set("limit", String(params.limit ?? 40));
    query.set("offset", String(params.offset ?? 0));
    return request<StudyList>(`/api/studies?${query.toString()}`);
  },

  getStudy: (id: string) => request<StudyDetail>(`/api/studies/${encodeURIComponent(id)}`),

  retryStudy: (id: string) =>
    request<StudyDetail>(`/api/studies/${encodeURIComponent(id)}/retry`, { method: "POST" }),

  reviewStudy: (id: string, decision: "approve" | "reject") =>
    request<StudyDetail>(`/api/studies/${encodeURIComponent(id)}/review`, {
      method: "POST",
      ...json({ decision }),
    }),

  upload: (files: File[]) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    body.append("confirm_deidentified", "true");
    return request<UploadOutcome>("/api/uploads", { method: "POST", body });
  },

  /** Thumbnails need the session header, so they are fetched as blobs. */
  fetchThumbnail: async (url: string): Promise<Blob> => {
    const headers = new Headers();
    const token = getSessionToken();
    if (token) headers.set("X-Session-Token", token);
    const response = await fetch(url, { headers, credentials: "same-origin" });
    if (!response.ok) throw new ApiError("Thumbnail unavailable", response.status);
    return response.blob();
  },

  getSettings: () => request<DicomwebSettings>("/api/settings/dicomweb"),

  accessMetadata: () => request<AccessMetadata>("/api/access-control/metadata"),
  listUsers: () => request<ManagedUser[]>("/api/access-control/users"),
  createUser: (body: { email: string; role: string; allowed_pages: string[] | null }) =>
    request<ManagedUser>("/api/access-control/users", { method: "POST", ...json(body) }),
  updateUser: (id: string, body: Record<string, unknown>) =>
    request<ManagedUser>(`/api/access-control/users/${encodeURIComponent(id)}`, {
      method: "PATCH",
      ...json(body),
    }),
  deactivateUser: (id: string) =>
    request<{ ok: boolean }>(`/api/access-control/users/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  listDomains: () => request<ManagedDomain[]>("/api/access-control/domains"),
  createDomain: (body: { domain: string; role: string; allowed_pages: string[] | null }) =>
    request<ManagedDomain>("/api/access-control/domains", { method: "POST", ...json(body) }),
  deleteDomain: (id: string) =>
    request<{ ok: boolean }>(`/api/access-control/domains/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  listAudit: () => request<AuditEntry[]>("/api/access-control/audit"),
};
