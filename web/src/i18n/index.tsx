import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { en } from "./locales/en";
import { es } from "./locales/es";
import { ptBR } from "./locales/pt-BR";
import type { Dictionary } from "./locales/pt-BR";

export type { Dictionary };

export const LOCALES = { "pt-BR": ptBR, en, es } as const;
export type Locale = keyof typeof LOCALES;

/** Flag and name per locale, for the switcher. */
export const LOCALE_META: Record<Locale, { flag: string; name: string }> = {
  "pt-BR": { flag: "🇧🇷", name: "Português" },
  en: { flag: "🇺🇸", name: "English" },
  es: { flag: "🇪🇸", name: "Español" },
};

const STORAGE_KEY = "chester.locale";
const DEFAULT_LOCALE: Locale = "pt-BR";

const readStoredLocale = (): Locale => {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && stored in LOCALES) return stored as Locale;
  } catch {
    /* Blocked storage falls back to the default. */
  }
  // Match on the language subtag only: "es-AR" and "en-GB" should still land on
  // Spanish and English rather than falling through to the default.
  const preferred = (typeof navigator !== "undefined" ? navigator.language : "")
    .toLowerCase()
    .split("-")[0];
  if (preferred === "en") return "en";
  if (preferred === "es") return "es";
  return DEFAULT_LOCALE;
};

/** Substitute {name} placeholders. Keeps interpolation out of every call site. */
export const interpolate = (template: string, values: Record<string, string | number>): string =>
  template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in values ? String(values[key]) : match,
  );

interface I18nValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: Dictionary;
  format: (template: string, values: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readStoredLocale);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* The choice simply does not persist. */
    }
    document.documentElement.lang = next;
  }, []);

  const value = useMemo<I18nValue>(
    () => ({ locale, setLocale, t: LOCALES[locale], format: interpolate }),
    [locale, setLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (value === null) throw new Error("useI18n must be used inside I18nProvider");
  return value;
}
