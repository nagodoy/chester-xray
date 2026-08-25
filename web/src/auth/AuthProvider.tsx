import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api, getSessionToken } from "../api/client";
import type { Access, Page } from "../api/types";

interface AuthValue {
  access: Access | null;
  loading: boolean;
  /** Whether a stored session exists, so callers can skip gating without one. */
  hasToken: boolean;
  signIn: (access: Access) => void;
  signOut: () => Promise<void>;
  can: (page: Page) => boolean;
}

const AuthContext = createContext<AuthValue | null>(null);

/**
 * Whether the interface should offer a page. The server enforces the same rule on
 * every request; this only decides what is worth showing.
 */
export const canAccess = (access: Access | null, page: Page): boolean => {
  if (access === null) return false;
  if (page === "access-control") return access.is_admin;
  if (access.is_admin) return true;
  if (access.allowed_pages === null) return true;
  return access.allowed_pages.includes(page);
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [access, setAccess] = useState<Access | null>(null);
  const [hasToken] = useState(() => getSessionToken() !== null);
  const [loading, setLoading] = useState(hasToken);

  useEffect(() => {
    let cancelled = false;

    const restore = async () => {
      if (!hasToken) return;
      try {
        const result = await api.validateSession();
        if (!cancelled) setAccess(result.access);
      } catch {
        if (!cancelled) setAccess(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void restore();
    return () => {
      cancelled = true;
    };
  }, [hasToken]);

  const signOut = useCallback(async () => {
    await api.logout();
    setAccess(null);
  }, []);

  const value = useMemo<AuthValue>(
    () => ({
      access,
      loading,
      hasToken,
      signIn: setAccess,
      signOut,
      can: (page: Page) => canAccess(access, page),
    }),
    [access, loading, hasToken, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
