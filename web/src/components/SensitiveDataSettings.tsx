import { EyeOff } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type { SensitiveDataPolicy } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useI18n } from "../i18n";
import { useSensitiveData } from "../privacy";

/**
 * Which roles may reveal the identifying fields on screen.
 *
 * Readable by anyone who can open this page, because the list is what explains
 * why a colleague has the toggle in their sidebar and you do not. Only an
 * administrator can change it, and the administrator row is fixed: a
 * configuration someone can edit themselves out of is one nobody can put back.
 *
 * What this governs is the *interface*. The values are already in the responses
 * the browser fetched, so this is protection from someone reading the screen,
 * not from someone reading the API. The subtitle says so rather than letting an
 * operator infer more.
 */
export function SensitiveDataSettings() {
  const { t, format } = useI18n();
  const { signIn } = useAuth();
  const { reveal } = useSensitiveData();

  const [state, setState] = useState<SensitiveDataPolicy | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setError("");
      setState(await api.getSensitiveDataPolicy());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const choose = (role: string) => {
    if (!state?.editable || busy) return;

    const chosen = state.roles
      .filter((row) => row.selectable && (row.value === role ? !row.allowed : row.allowed))
      .map((row) => row.value);

    setBusy(true);
    api
      .setSensitiveDataPolicy(chosen)
      .then(async (saved) => {
        setState(saved);
        // The signed-in caller's own may_reveal_sensitive is now stale -- an
        // administrator who just granted their own role would otherwise not see
        // the sidebar toggle until the next sign-in. Re-reading the session is
        // cheaper than explaining that.
        try {
          const refreshed = await api.validateSession();
          signIn(refreshed.access);
        } catch {
          /* The policy is saved either way; the toggle catches up on reload. */
        }
      })
      .catch((caught: unknown) =>
        setError(caught instanceof Error ? caught.message : String(caught)),
      )
      .finally(() => setBusy(false));
  };

  if (!state) return null;

  const editable = state.editable;

  return (
    <section className="panel sensitive-data">
      <div className="retention-heading">
        <EyeOff size={15} aria-hidden />
        <h3>{t.privacy.title}</h3>
      </div>
      <p className="retention-note">{editable ? t.privacy.subtitle : t.privacy.subtitleReadOnly}</p>

      <div className="sensitive-roles">
        {state.roles.map((row) => (
          <button
            key={row.value}
            type="button"
            className={row.allowed ? "window-option is-chosen" : "window-option"}
            aria-pressed={row.allowed}
            disabled={!editable || !row.selectable || busy}
            onClick={() => choose(row.value)}
          >
            {row.label}
            {!row.selectable && (
              <span className="sensitive-fixed"> · {t.privacy.alwaysAllowed}</span>
            )}
          </button>
        ))}
      </div>

      {state.updated_by && (
        <p className="retention-swept">
          {format(t.privacy.updatedBy, { who: reveal(state.updated_by, t.common.none) })}
        </p>
      )}
      {error && <p className="retention-error">{error}</p>}
    </section>
  );
}
