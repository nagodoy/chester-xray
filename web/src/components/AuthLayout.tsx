import { Activity } from "lucide-react";
import type { ReactNode } from "react";

import { useI18n } from "../i18n";
import { LocaleSwitch } from "./LocaleSwitch";

/**
 * The shell both sign-in steps share: the brand panel on the left, the step on
 * the right, and the step's own footnote under a rule.
 *
 * Keeping it in one place is what makes the two steps feel like one screen
 * changing rather than two screens swapping -- only the right column moves.
 */
export function AuthLayout({ children, footnote }: { children: ReactNode; footnote?: ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="auth-shell auth-split">
      <aside className="auth-aside">
        <div className="auth-aside-brand">
          <div className="auth-logo" aria-hidden>
            <Activity size={24} strokeWidth={2.25} />
          </div>
          <p className="auth-eyebrow">{t.auth.asideEyebrow}</p>
          <h1 className="auth-display">{t.brand.name}</h1>
          <p className="auth-aside-tagline">{t.auth.asideTagline}</p>
        </div>

        <div className="auth-aside-notes">
          <p className="auth-aside-note">
            <i className="auth-dot" aria-hidden />
            {t.auth.asideNote}
          </p>
          <p className="auth-aside-research">{t.auth.researchOnly}</p>
        </div>
      </aside>

      <main className="auth-main">
        <div className="auth-locale-corner">
          <LocaleSwitch compact />
        </div>
        <section className="auth-panel">
          {children}
          {footnote && <p className="auth-footnote">{footnote}</p>}
        </section>
      </main>
    </div>
  );
}
