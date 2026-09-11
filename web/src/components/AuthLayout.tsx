import { Activity } from "lucide-react";
import type { ReactNode } from "react";

import { useI18n } from "../i18n";
import { LocaleSwitch } from "./LocaleSwitch";

/**
 * The shell both sign-in steps share: a split screen with the brand held on
 * the left and the current step on the right.
 *
 * The left half never changes, so moving from email to code reads as one
 * screen advancing rather than two screens swapping. The hairline between the
 * halves is the only rule on the page -- everything else separates by space.
 */
export function AuthLayout({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="auth-split">
      <aside className="auth-aside">
        <div className="auth-aside-brand">
          <div className="auth-logo" aria-hidden>
            <Activity size={52} strokeWidth={2} />
          </div>
          <p className="auth-eyebrow">{t.auth.protectedEnvironment}</p>
          <h1 className="auth-wordmark">{t.brand.name}</h1>
          <p className="auth-aside-tagline">{t.brand.tagline}</p>
        </div>

        <div className="auth-aside-foot">
          <p className="auth-aside-note">
            <span className="auth-dot" aria-hidden />
            {t.auth.restrictedNote}
          </p>
          <p className="auth-aside-fineprint">{t.auth.researchOnly}</p>
          <LocaleSwitch compact />
        </div>
      </aside>

      <main className="auth-main">
        <section className="auth-panel">{children}</section>
      </main>
    </div>
  );
}
