import { Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type { NetworkLogRetention } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useI18n } from "../i18n";
import { ConfirmDialog } from "./ConfirmDialog";

/**
 * How long the network log is kept.
 *
 * The window is enforced by a routine in the worker, not by this panel: the
 * button only brings the next sweep forward. That is why the copy says what the
 * routine does rather than describing a deletion the operator is about to
 * perform -- the rows would have gone within the interval anyway.
 *
 * Anyone who can see the page can read the window, because the count is what
 * explains where yesterday's entries went. Only an administrator can change it
 * or apply it early.
 */
export function RetentionPanel({ onPurged }: { onPurged?: () => void }) {
  const { t, format, locale } = useI18n();
  const { access } = useAuth();
  const isAdmin = access?.is_admin ?? false;

  const [state, setState] = useState<NetworkLogRetention | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(async () => {
    try {
      setError("");
      setState(await api.getRetention());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const choose = (hours: number) => {
    if (!isAdmin || busy || hours === state?.hours) return;
    setBusy(true);
    api
      .setRetention(hours)
      .then(setState)
      .catch((caught: unknown) =>
        setError(caught instanceof Error ? caught.message : String(caught)),
      )
      .finally(() => setBusy(false));
  };

  const purge = () => {
    setBusy(true);
    api
      .purgeNetworkLogs()
      .then((outcome) => {
        setState(outcome.retention);
        setConfirming(false);
        onPurged?.();
      })
      .catch((caught: unknown) =>
        setError(caught instanceof Error ? caught.message : String(caught)),
      )
      .finally(() => setBusy(false));
  };

  if (!state) return null;

  const expiring = state.expiring;

  return (
    <section className="panel retention">
      <div className="retention-heading">
        <Trash2 size={15} aria-hidden />
        <h3>{t.retention.title}</h3>
      </div>
      <p className="retention-note">
        {isAdmin ? t.retention.subtitle : t.retention.subtitleReadOnly}
      </p>

      <div className="retention-window">
        <span className="retention-label">{t.retention.keepFor}</span>
        {state.options.map((hours) => (
          <button
            key={hours}
            type="button"
            className={hours === state.hours ? "window-option is-chosen" : "window-option"}
            aria-pressed={hours === state.hours}
            disabled={!isAdmin || busy}
            onClick={() => choose(hours)}
          >
            {format(t.retention.hours, { hours })}
          </button>
        ))}
      </div>

      <div className="retention-actions">
        <span className={expiring > 0 ? "retention-count is-pending" : "retention-count"}>
          {expiring > 0
            ? format(expiring === 1 ? t.retention.oneExpiring : t.retention.expiring, {
                count: expiring,
              })
            : t.retention.nothingExpiring}
        </span>
        {isAdmin && (
          <button
            type="button"
            className="btn btn-danger"
            disabled={busy || expiring === 0}
            onClick={() => setConfirming(true)}
          >
            <Trash2 size={14} aria-hidden />
            {t.retention.purgeNow}
          </button>
        )}
      </div>

      {state.last_swept_at && (
        <p className="retention-swept">
          {format(t.retention.lastSwept, {
            when: new Date(state.last_swept_at).toLocaleString(locale),
          })}
        </p>
      )}
      {error && <p className="retention-error">{error}</p>}

      {confirming && (
        <ConfirmDialog
          title={t.retention.confirmTitle}
          body={format(t.retention.confirmBody, { count: expiring, hours: state.hours })}
          confirmLabel={t.retention.purgeNow}
          busy={busy}
          onConfirm={purge}
          onCancel={() => setConfirming(false)}
        />
      )}
    </section>
  );
}
