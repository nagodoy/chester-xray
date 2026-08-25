import {
  AlertTriangle,
  ClipboardList,
  Globe,
  LogOut,
  Settings2,
  ShieldCheck,
} from "lucide-react";
import { Link, useLocation, useSearch } from "wouter";
import type { ReactNode } from "react";

import { useAuth } from "../auth/AuthProvider";
import { useI18n } from "../i18n";
import type { Locale } from "../i18n";

function Brand({ light = false }: { light?: boolean }) {
  const { t } = useI18n();
  return (
    <div className={light ? "brand light" : "brand"}>
      <span className="brand-mark" aria-hidden>
        ⌁
      </span>
      <div>
        {t.brand.name}
        <small>{t.brand.tagline}</small>
      </div>
    </div>
  );
}

function LocaleSwitch() {
  const { locale, setLocale } = useI18n();
  return (
    <label className="locale-switch">
      <Globe size={14} aria-hidden />
      <span className="visually-hidden">Language</span>
      <select value={locale} onChange={(event) => setLocale(event.target.value as Locale)}>
        <option value="pt-BR">PT</option>
        <option value="en">EN</option>
      </select>
    </label>
  );
}

function Sidebar() {
  const { can, signOut } = useAuth();
  const { t } = useI18n();
  const [location] = useLocation();
  const search = useSearch();

  const reviewActive =
    location === "/worklist" && new URLSearchParams(search).get("status") === "needs_review";
  const worklistActive = location === "/worklist" && !reviewActive;

  return (
    <aside className="sidebar">
      <Brand />

      <div className="sidebar-label">{t.nav.research}</div>
      <nav className="nav" aria-label={t.nav.research}>
        {can("worklist") && (
          <Link
            href="/worklist"
            className={worklistActive ? "active" : ""}
            aria-current={worklistActive ? "page" : undefined}
          >
            <ClipboardList size={16} aria-hidden />
            <span>{t.nav.worklist}</span>
          </Link>
        )}
        {can("review") && (
          <Link
            href="/worklist?status=needs_review"
            className={reviewActive ? "active" : ""}
            aria-current={reviewActive ? "page" : undefined}
          >
            <AlertTriangle size={16} aria-hidden />
            <span>{t.nav.review}</span>
          </Link>
        )}
      </nav>

      <div className="sidebar-label">{t.nav.system}</div>
      <nav className="nav nav-secondary" aria-label={t.nav.system}>
        {can("settings") && (
          <Link
            href="/settings"
            className={location === "/settings" ? "active" : ""}
            aria-current={location === "/settings" ? "page" : undefined}
          >
            <Settings2 size={16} aria-hidden />
            <span>{t.nav.settings}</span>
          </Link>
        )}
        {can("access-control") && (
          <Link
            href="/access-control"
            className={location === "/access-control" ? "active" : ""}
            aria-current={location === "/access-control" ? "page" : undefined}
          >
            <ShieldCheck size={16} aria-hidden />
            <span>{t.nav.accessControl}</span>
          </Link>
        )}
      </nav>

      <div className="sidebar-note">
        <ShieldCheck size={15} aria-hidden />
        <strong>{t.nav.controlledEnvironment}</strong>
        <span>{t.nav.controlledNote}</span>
      </div>

      <button type="button" className="btn btn-subtle" onClick={() => void signOut()}>
        <LogOut size={15} aria-hidden />
        <span>{t.nav.signOut}</span>
      </button>
    </aside>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { access } = useAuth();
  const { t, locale } = useI18n();
  const today = new Date().toLocaleDateString(locale, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });

  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="eyebrow">
            {t.nav.readingRoom} / {today}
          </div>
          <div className="topbar-right">
            <LocaleSwitch />
            <div className="user-chip">
              <span>{access?.email}</span>
              <div className="avatar" aria-hidden>
                {(access?.email ?? "?").slice(0, 1).toUpperCase()}
              </div>
            </div>
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}

export function PageHeading({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow: string;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </div>
  );
}
