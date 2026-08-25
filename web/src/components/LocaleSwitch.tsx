import { LOCALE_META, useI18n } from "../i18n";
import type { Locale } from "../i18n";

const ORDER: Locale[] = ["pt-BR", "en", "es"];

/**
 * Flag buttons, shared by the sign-in screens and the application shell.
 *
 * The flag alone is not an accessible label -- an emoji reads out as its
 * character name, and a flag is a country rather than a language -- so each
 * button carries the language name for assistive technology and as its tooltip.
 */
export function LocaleSwitch({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale } = useI18n();

  return (
    <div
      className={compact ? "locale-switch is-compact" : "locale-switch"}
      role="group"
      aria-label="Language"
    >
      {ORDER.map((code) => {
        const { flag, name } = LOCALE_META[code];
        const active = code === locale;
        return (
          <button
            key={code}
            type="button"
            className={active ? "locale-option is-active" : "locale-option"}
            aria-pressed={active}
            title={name}
            onClick={() => setLocale(code)}
          >
            <span aria-hidden>{flag}</span>
            <span className="visually-hidden">{name}</span>
          </button>
        );
      })}
    </div>
  );
}
