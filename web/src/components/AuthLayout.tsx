import { Activity, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import { useI18n } from "../i18n";
import type { Locale } from "../i18n";

const LOCALE_LABELS: Record<Locale, string> = { "pt-BR": "PT", en: "EN" };

function LocaleSwitch() {
  const { locale, setLocale } = useI18n();
  return (
    <div className="auth-locales" role="group" aria-label="Language">
      {(Object.keys(LOCALE_LABELS) as Locale[]).map((code) => (
        <button
          key={code}
          type="button"
          className={code === locale ? "auth-locale is-active" : "auth-locale"}
          aria-pressed={code === locale}
          onClick={() => setLocale(code)}
        >
          {LOCALE_LABELS[code]}
        </button>
      ))}
    </div>
  );
}

/**
 * The shell both sign-in steps share: brand mark, card, footer note.
 *
 * Keeping it in one place is what makes the two steps feel like one screen
 * changing rather than two screens swapping.
 */
export function AuthLayout({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="auth-shell">
      <div className="auth-wrap">
        <header className="auth-brand">
          <div className="auth-logo" aria-hidden>
            <Activity size={26} strokeWidth={2.25} />
          </div>
          <h1>{t.brand.name}</h1>
          <p>{t.brand.tagline}</p>
          <LocaleSwitch />
        </header>

        <section className="auth-card">{children}</section>

        <p className="auth-footnote">
          <ShieldCheck size={14} aria-hidden />
          {t.auth.researchOnly}
        </p>
      </div>
    </div>
  );
}
