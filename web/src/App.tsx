import { ShieldCheck } from "lucide-react";
import { Redirect, Route, Switch } from "wouter";
import type { ReactNode } from "react";

import type { Page } from "./api/types";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import { I18nProvider, useI18n } from "./i18n";
import { AccessControl } from "./pages/AccessControl";
import { SettingsPage } from "./pages/SettingsPage";
import { SignIn } from "./pages/SignIn";
import { StudyDetail } from "./pages/StudyDetail";
import { Worklist } from "./pages/Worklist";
import "./styles/app.css";

/** Pages a redirect may land on, in the order they are tried. */
const FALLBACK_PAGES: Page[] = ["worklist", "settings", "access-control"];
const PAGE_PATHS: Record<Page, string> = {
  worklist: "/worklist",
  review: "/worklist?status=needs_review",
  "study-detail": "/worklist",
  upload: "/worklist",
  settings: "/settings",
  "access-control": "/access-control",
};

function Loading() {
  const { t } = useI18n();
  return (
    <div className="auth-shell">
      <div className="auth-loading">{t.common.checkingAccess}</div>
    </div>
  );
}

function AccessDenied() {
  const { signOut } = useAuth();
  const { t } = useI18n();
  return (
    <div className="auth-shell">
      <div className="access-denied">
        <ShieldCheck size={28} aria-hidden />
        <h1>{t.auth.accessDenied}</h1>
        <p>{t.auth.accessDeniedBody}</p>
        <button type="button" className="btn btn-primary" onClick={() => void signOut()}>
          {t.nav.signOut}
        </button>
      </div>
    </div>
  );
}

/**
 * Gate a route on a page permission. This is convenience, not enforcement: the
 * server checks the same permission on every request.
 */
function Protected({ page, children }: { page: Page; children: ReactNode }) {
  const { access, loading, can } = useAuth();

  if (loading) return <Loading />;
  if (!access) return <Redirect to="/sign-in" />;
  if (can(page)) return <>{children}</>;

  const fallback = FALLBACK_PAGES.find((candidate) => can(candidate));
  return fallback ? <Redirect to={PAGE_PATHS[fallback]} /> : <AccessDenied />;
}

function Home() {
  const { access, loading } = useAuth();
  if (loading) return <Loading />;
  return <Redirect to={access ? "/worklist" : "/sign-in"} />;
}

function Routes() {
  const { access } = useAuth();
  return (
    <Switch>
      <Route path="/" component={Home} />
      <Route path="/sign-in">{access ? <Redirect to="/worklist" /> : <SignIn />}</Route>
      <Route path="/worklist">
        <Protected page="worklist">
          <Worklist />
        </Protected>
      </Route>
      <Route path="/studies/:id">
        <Protected page="study-detail">
          <StudyDetail />
        </Protected>
      </Route>
      <Route path="/settings">
        <Protected page="settings">
          <SettingsPage />
        </Protected>
      </Route>
      <Route path="/access-control">
        <Protected page="access-control">
          <AccessControl />
        </Protected>
      </Route>
      <Route>
        <Redirect to="/" />
      </Route>
    </Switch>
  );
}

export default function App() {
  return (
    <I18nProvider>
      <AuthProvider>
        <Routes />
      </AuthProvider>
    </I18nProvider>
  );
}
