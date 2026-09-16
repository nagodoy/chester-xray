import { Eye, EyeOff } from "lucide-react";

import { useI18n } from "../i18n";
import { useSensitiveData } from "../privacy";

/**
 * Show or hide the identifying fields, from the foot of the sidebar.
 *
 * Absent rather than disabled for a role the organization has not granted: a
 * disabled control invites someone to ask why it is disabled, and the answer is
 * a policy they cannot change. The roles that may reveal are listed on the
 * settings page, which is where that question belongs.
 */
export function SensitiveDataToggle() {
  const { mayReveal, revealed, toggle } = useSensitiveData();
  const { t } = useI18n();

  if (!mayReveal) return null;

  const label = revealed ? t.privacy.hide : t.privacy.show;

  return (
    <button
      type="button"
      className={revealed ? "sidebar-toggle is-revealed" : "sidebar-toggle"}
      aria-pressed={revealed}
      title={label}
      onClick={toggle}
    >
      {revealed ? <Eye size={16} aria-hidden /> : <EyeOff size={16} aria-hidden />}
      <span>{t.privacy.sidebar}</span>
      <span className="visually-hidden">{label}</span>
    </button>
  );
}
