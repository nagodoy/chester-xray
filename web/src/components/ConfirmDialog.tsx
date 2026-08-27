import { AlertTriangle } from "lucide-react";
import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

import { useI18n } from "../i18n";

/**
 * A modal for an action that cannot be undone.
 *
 * Focus moves to Cancel rather than Confirm: the dialog exists because the
 * action is destructive, so the safe button is the one a stray Enter should
 * hit. Escape and the backdrop both cancel; neither confirms.
 */
export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  busy = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="dialog-backdrop" onClick={() => !busy && onCancel()}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="dialog-title">
          <span className="dialog-icon" aria-hidden>
            <AlertTriangle size={16} />
          </span>
          <h2>{title}</h2>
        </div>
        <div className="dialog-body">{body}</div>
        <div className="dialog-actions">
          <button
            type="button"
            className="btn btn-subtle"
            ref={cancelRef}
            disabled={busy}
            onClick={onCancel}
          >
            {t.common.cancel}
          </button>
          <button
            type="button"
            className="btn btn-danger-strong"
            disabled={busy}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
