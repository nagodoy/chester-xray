import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { api } from "../api/client";
import type { AccessMetadata, AuditEntry, ManagedDomain, ManagedUser } from "../api/types";
import { AppShell, PageHeading } from "../components/AppShell";
import { ErrorBox, Skeleton } from "../components/common";
import { useI18n } from "../i18n";

const AUDIT_LIMIT = 20;

const parsePages = (raw: string): string[] | null => {
  const pages = raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return pages.length > 0 ? pages : null;
};

export function AccessControl() {
  const { t, format, locale } = useI18n();

  const [metadata, setMetadata] = useState<AccessMetadata>({ roles: [], pages: [] });
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [domains, setDomains] = useState<ManagedDomain[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  const [email, setEmail] = useState("");
  const [domain, setDomain] = useState("");
  const [role, setRole] = useState("technician");
  const [pages, setPages] = useState("");

  const load = useCallback(async () => {
    try {
      setError("");
      const [nextMetadata, nextUsers, nextDomains, nextAudit] = await Promise.all([
        api.accessMetadata(),
        api.listUsers(),
        api.listDomains(),
        api.listAudit(),
      ]);
      setMetadata(nextMetadata);
      setUsers(nextUsers);
      setDomains(nextDomains);
      setAudit(nextAudit);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (action: () => Promise<unknown>) => {
    try {
      setError("");
      await action();
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const addUser = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      await api.createUser({ email, role, allowed_pages: parsePages(pages) });
      setEmail("");
      setPages("");
    });
  };

  const addDomain = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      await api.createDomain({ domain, role, allowed_pages: parsePages(pages) });
      setDomain("");
      setPages("");
    });
  };

  const editPages = (current: string[] | null, apply: (next: string[] | null) => Promise<unknown>) => {
    const answer = window.prompt(t.access.editPagesPrompt, (current ?? []).join(", "));
    if (answer === null) return;
    void run(() => apply(parsePages(answer)));
  };

  return (
    <AppShell>
      <PageHeading eyebrow={t.access.eyebrow} title={t.access.title} subtitle={t.access.subtitle} />

      {error && <ErrorBox title={t.access.title} message={error} onRetry={() => void load()} />}
      {!loaded && <Skeleton count={2} />}

      <section className="panel">
        <div className="panel-title">
          <h3>{t.access.newUser}</h3>
        </div>
        <form className="toolbar" onSubmit={addUser}>
          <input
            className="input"
            type="email"
            required
            value={email}
            placeholder={t.access.emailPlaceholder}
            onChange={(event) => setEmail(event.target.value)}
          />
          <select
            className="select"
            value={role}
            onChange={(event) => setRole(event.target.value)}
          >
            {metadata.roles.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <input
            className="input"
            value={pages}
            placeholder={t.access.pagesPlaceholder}
            onChange={(event) => setPages(event.target.value)}
          />
          <button type="submit" className="btn btn-primary">
            {t.common.add}
          </button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-title">
          <h3>{t.access.newDomain}</h3>
        </div>
        <form className="toolbar" onSubmit={addDomain}>
          <input
            className="input"
            required
            value={domain}
            placeholder={t.access.domainPlaceholder}
            onChange={(event) => setDomain(event.target.value)}
          />
          <select
            className="select"
            value={role}
            onChange={(event) => setRole(event.target.value)}
          >
            {metadata.roles.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <button type="submit" className="btn btn-primary">
            {t.common.add}
          </button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-title">
          <h3>{t.access.users}</h3>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t.access.email}</th>
                <th>{t.access.role}</th>
                <th>{t.access.pages}</th>
                <th>{t.access.statusColumn}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id}>
                  <td>
                    {user.email}
                    {user.is_env_admin && (
                      <small className="mono"> · {t.access.environmentManaged}</small>
                    )}
                  </td>
                  <td>{user.role_label}</td>
                  <td>{user.allowed_pages?.join(", ") ?? t.common.all}</td>
                  <td>{user.active ? t.common.active : t.common.inactive}</td>
                  <td className="row-actions">
                    {!user.is_env_admin && (
                      <>
                        <button
                          type="button"
                          className="btn btn-subtle"
                          onClick={() =>
                            editPages(user.allowed_pages, (next) =>
                              api.updateUser(user.id, { allowed_pages: next }),
                            )
                          }
                        >
                          {t.access.editPages}
                        </button>
                        <button
                          type="button"
                          className="btn btn-danger"
                          disabled={!user.active}
                          onClick={() => {
                            if (window.confirm(format(t.access.confirmDeactivate, { email: user.email }))) {
                              void run(() => api.deactivateUser(user.id));
                            }
                          }}
                        >
                          {t.common.remove}
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="panel-title">
          <h3>{t.access.domains}</h3>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t.access.domain}</th>
                <th>{t.access.role}</th>
                <th>{t.access.pages}</th>
                <th>{t.access.statusColumn}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {domains.map((rule) => (
                <tr key={rule.id}>
                  <td>{rule.domain}</td>
                  <td>{rule.role_label}</td>
                  <td>{rule.allowed_pages?.join(", ") ?? t.common.all}</td>
                  <td>{rule.active ? t.common.active : t.common.inactive}</td>
                  <td className="row-actions">
                    <button
                      type="button"
                      className="btn btn-danger"
                      onClick={() => {
                        if (window.confirm(format(t.access.confirmDeleteDomain, { domain: rule.domain }))) {
                          void run(() => api.deleteDomain(rule.id));
                        }
                      }}
                    >
                      {t.common.remove}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="panel-title">
          <h3>{t.access.audit}</h3>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t.access.when}</th>
                <th>{t.access.actor}</th>
                <th>{t.access.action}</th>
                <th>{t.access.target}</th>
              </tr>
            </thead>
            <tbody>
              {audit.slice(0, AUDIT_LIMIT).map((entry) => (
                <tr key={entry.id}>
                  <td className="mono">{new Date(entry.created_at).toLocaleString(locale)}</td>
                  <td>{entry.actor_email}</td>
                  <td>{entry.action}</td>
                  <td>{entry.target_key}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </AppShell>
  );
}
