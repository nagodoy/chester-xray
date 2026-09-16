import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { useAuth } from "../auth/AuthProvider";
import { useI18n } from "../i18n";

/**
 * Hiding the identifying fields on screen.
 *
 * This is screen privacy, not access control. The values are already in the JSON
 * the browser fetched -- masking happens after the response arrives, so anyone
 * with developer tools reads them either way. What it protects against is the
 * person looking at the screen: a reading room, a demonstration, a shared window.
 * The copy in the interface says "hide", never "protect", for that reason.
 *
 * Whether the toggle exists at all is the server's decision, carried on
 * `access.may_reveal_sensitive`. The preference itself never leaves the browser.
 */

const STORAGE_KEY = "chester.reveal-sensitive";

/**
 * A fixed width, never one derived from the value. A mask as long as what it
 * hides tells a reader how long the hidden thing is, which for a date or a sex is
 * most of the way to reading it.
 */
const MASK = "••••••••";

const readStoredPreference = (): boolean => {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    /* Private mode and blocked storage both land here. Hidden is the safe side. */
    return false;
  }
};

interface SensitiveDataValue {
  /** Whether this role is allowed to reveal at all, as resolved by the server. */
  mayReveal: boolean;
  /** Whether identifying fields are showing right now. */
  revealed: boolean;
  toggle: () => void;
  /**
   * The value, or the mask, for places that need a string rather than an element:
   * `title`, `aria-label`, and text built by template. Prefer `<Sensitive/>` where
   * an element will do, because it also says "hidden" to a screen reader.
   */
  reveal: (value: string | null | undefined, fallback: string) => string;
}

const SensitiveDataContext = createContext<SensitiveDataValue | null>(null);

export function SensitiveDataProvider({ children }: { children: ReactNode }) {
  const { access } = useAuth();
  const [stored, setStored] = useState<boolean>(readStoredPreference);

  const mayReveal = access?.may_reveal_sensitive ?? false;

  // Derived, never stored on its own. An administrator who takes the permission
  // away from a role should not leave that role's screen revealed until someone
  // thinks to clear a flag: the next render recomputes this as false, and the
  // stored preference simply sits there inert until the permission returns.
  const revealed = mayReveal && stored;

  const toggle = useCallback(() => {
    setStored((previous) => {
      const next = !previous;
      try {
        if (next) localStorage.setItem(STORAGE_KEY, "1");
        else localStorage.removeItem(STORAGE_KEY);
      } catch {
        /* The choice simply does not persist across reloads. */
      }
      return next;
    });
  }, []);

  const value = useMemo<SensitiveDataValue>(
    () => ({
      mayReveal,
      revealed,
      toggle,
      reveal: (raw, fallback) => {
        if (raw === null || raw === undefined || raw === "") return fallback;
        return revealed ? raw : MASK;
      },
    }),
    [mayReveal, revealed, toggle],
  );

  return (
    <SensitiveDataContext.Provider value={value}>{children}</SensitiveDataContext.Provider>
  );
}

/**
 * What to call the patient a study belongs to.
 *
 * The name when the server sent one, which it does only for a caller whose role
 * may see it, and the pseudonym otherwise -- a study filed from a de-identified
 * image has no name to show, and neither does a row a consultant is reading. The
 * value still goes through the mask; this only chooses what the mask is hiding.
 */
export const patientLabel = (study: {
  patient_name: string | null;
  patient_id: string | null;
}): string | null => study.patient_name || study.patient_id;

export function useSensitiveData(): SensitiveDataValue {
  const value = useContext(SensitiveDataContext);
  if (value === null) {
    throw new Error("useSensitiveData must be used inside SensitiveDataProvider");
  }
  return value;
}

/**
 * One identifying value, masked or not.
 *
 * The mask is a row of bullets to look at and the word "oculto" to listen to: a
 * screen reader announcing eight bullet characters one by one is worse than no
 * masking at all, because the reader cannot tell a hidden field from a broken one.
 */
export function Sensitive({
  value,
  fallback,
}: {
  value: string | null | undefined;
  fallback: string;
}) {
  const { revealed } = useSensitiveData();
  const { t } = useI18n();

  if (value === null || value === undefined || value === "") return <>{fallback}</>;
  if (revealed) return <>{value}</>;

  return (
    <span className="masked" aria-label={t.privacy.hidden} title={t.privacy.hidden}>
      <span aria-hidden>{MASK}</span>
    </span>
  );
}
