import { useCallback, useEffect, useState } from "react";
import { Link } from "wouter";

import { api } from "../api/client";
import type { NetworkLogEntry } from "../api/types";
import { AppShell, PageHeading } from "../components/AppShell";
import { ErrorBox, Skeleton } from "../components/common";
import { useI18n } from "../i18n";

/** Reuses the worklist pills: a delivery either landed or it did not. */
const STATUS_CLASS: Record<NetworkLogEntry["status"], string> = {
  success: "pill pill-completed",
  failure: "pill pill-error",
  duplicate: "pill pill-needs_review",
};

function StudyLink({ id, label }: { id: string | null; label: string }) {
  const { t } = useI18n();
  if (!id) return <span>{t.common.none}</span>;
  return (
    <Link href={`/studies/${id}`} className="mono" title={label}>
      {id.slice(0, 8)}
    </Link>
  );
}

export function NetworkLogs() {
  const { t, locale } = useI18n();

  const [received, setReceived] = useState<NetworkLogEntry[]>([]);
  const [sent, setSent] = useState<NetworkLogEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setError("");
      const [inbound, outbound] = await Promise.all([
        api.listNetworkLogs("received"),
        api.listNetworkLogs("sent"),
      ]);
      setReceived(inbound.items);
      setSent(outbound.items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const when = (value: string) => new Date(value).toLocaleString(locale);
  const channel = (value: string) =>
    (t.networkLogs.channels as Record<string, string>)[value] ?? value;
  const situation = (entry: NetworkLogEntry) => (
    <span className={STATUS_CLASS[entry.status]}>
      {t.networkLogs.status[entry.status] ?? entry.status}
    </span>
  );

  return (
    <AppShell>
      <PageHeading
        eyebrow={t.networkLogs.eyebrow}
        title={t.networkLogs.title}
        subtitle={t.networkLogs.subtitle}
        actions={
          <button type="button" className="btn btn-subtle" onClick={() => void load()}>
            {t.networkLogs.refresh}
          </button>
        }
      />

      {error && (
        <ErrorBox title={t.networkLogs.title} message={error} onRetry={() => void load()} />
      )}
      {!loaded && <Skeleton count={2} />}

      <section className="panel">
        <div className="panel-title">
          <h3>{t.networkLogs.received}</h3>
          <span>{received.length}</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t.networkLogs.when}</th>
                <th>{t.networkLogs.origin}</th>
                <th>{t.networkLogs.channel}</th>
                <th>{t.networkLogs.actor}</th>
                <th>{t.networkLogs.study}</th>
                <th>{t.networkLogs.reference}</th>
                <th>{t.networkLogs.situation}</th>
                <th>{t.networkLogs.message}</th>
              </tr>
            </thead>
            <tbody>
              {received.map((entry) => (
                <tr key={entry.id}>
                  <td className="mono">{when(entry.created_at)}</td>
                  <td className="mono">{entry.peer ?? t.common.none}</td>
                  <td>{channel(entry.channel)}</td>
                  <td className="cell-clip" title={entry.actor ?? ""}>
                    {entry.actor ?? t.common.none}
                  </td>
                  <td>
                    <StudyLink id={entry.study_id} label={t.networkLogs.openStudy} />
                  </td>
                  <td className="mono cell-clip" title={entry.reference ?? ""}>
                    {entry.reference ?? t.common.none}
                  </td>
                  <td>{situation(entry)}</td>
                  <td className="cell-clip" title={entry.message ?? ""}>
                    {entry.message ?? t.common.none}
                  </td>
                </tr>
              ))}
              {loaded && received.length === 0 && (
                <tr>
                  <td colSpan={8}>{t.networkLogs.empty}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="panel-title">
          <h3>{t.networkLogs.sent}</h3>
          <span>{sent.length}</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t.networkLogs.when}</th>
                <th>{t.networkLogs.destination}</th>
                <th>{t.networkLogs.actor}</th>
                <th>{t.networkLogs.study}</th>
                <th>{t.networkLogs.reference}</th>
                <th>{t.networkLogs.situation}</th>
                <th>{t.networkLogs.message}</th>
              </tr>
            </thead>
            <tbody>
              {sent.map((entry) => (
                <tr key={entry.id}>
                  <td className="mono">{when(entry.created_at)}</td>
                  <td className="mono">{entry.peer ?? t.common.none}</td>
                  <td className="cell-clip" title={entry.actor ?? ""}>
                    {entry.actor ?? t.common.none}
                  </td>
                  <td>
                    <StudyLink id={entry.study_id} label={t.networkLogs.openStudy} />
                  </td>
                  <td className="mono cell-clip" title={entry.reference ?? ""}>
                    {entry.reference ?? t.common.none}
                  </td>
                  <td>{situation(entry)}</td>
                  <td className="cell-clip" title={entry.message ?? ""}>
                    {entry.message ?? t.common.none}
                  </td>
                </tr>
              ))}
              {loaded && sent.length === 0 && (
                <tr>
                  <td colSpan={7}>{t.networkLogs.empty}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </AppShell>
  );
}
