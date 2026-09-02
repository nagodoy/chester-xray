import { useCallback, useEffect, useState } from "react";
import { Link } from "wouter";

import { api } from "../api/client";
import type { NetworkLogEntry } from "../api/types";
import { AppShell, PageHeading } from "../components/AppShell";
import { ErrorBox, Pagination, Skeleton } from "../components/common";
import { useI18n } from "../i18n";

/** Reuses the worklist pills: a delivery either landed or it did not. */
const STATUS_CLASS: Record<NetworkLogEntry["status"], string> = {
  success: "pill pill-completed",
  failure: "pill pill-error",
  duplicate: "pill pill-needs_review",
};

/** Rows per page. A busy node logs thousands; one page is a readable window. */
const PAGE_SIZE = 25;

/**
 * One paged read of the log. Both tables want the same thing over a different
 * direction, so the fetch, the page cursor and the error live here once.
 *
 * Paging is done by the server rather than by slicing a big response: the table
 * grows without bound, and the endpoint already takes limit and offset.
 */
function useNetworkLogPage(direction: "received" | "sent") {
  const [items, setItems] = useState<NetworkLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    // Clicking through pages faster than the network answers would otherwise
    // let an older response land on top of a newer one.
    let cancelled = false;

    void (async () => {
      try {
        setError("");
        const response = await api.listNetworkLogs(direction, PAGE_SIZE, page * PAGE_SIZE);
        if (cancelled) return;
        setTotal(response.total);
        // Entries can disappear between reads, which leaves the cursor past the
        // end and the table blank. Step back and let the effect fetch again.
        const lastPage = Math.max(0, Math.ceil(response.total / PAGE_SIZE) - 1);
        if (page > lastPage) {
          setPage(lastPage);
          return;
        }
        setItems(response.items);
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [direction, page, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return { items, total, page, setPage, loaded, error, reload };
}

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

  const received = useNetworkLogPage("received");
  const sent = useNetworkLogPage("sent");

  const loaded = received.loaded && sent.loaded;
  const error = received.error || sent.error;
  const reload = () => {
    received.reload();
    sent.reload();
  };

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
          <button type="button" className="btn btn-subtle" onClick={reload}>
            {t.networkLogs.refresh}
          </button>
        }
      />

      {error && <ErrorBox title={t.networkLogs.title} message={error} onRetry={reload} />}
      {!loaded && <Skeleton count={2} />}

      <section className="panel">
        <div className="panel-title">
          <h3>{t.networkLogs.received}</h3>
          <span>{received.total}</span>
        </div>
        <div className="table-scroll table-scroll-rows">
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
              {received.items.map((entry) => (
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
              {received.loaded && received.items.length === 0 && (
                <tr>
                  <td colSpan={8}>{t.networkLogs.empty}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <Pagination
          page={received.page}
          pageSize={PAGE_SIZE}
          total={received.total}
          onChange={received.setPage}
        />
      </section>

      <section className="panel">
        <div className="panel-title">
          <h3>{t.networkLogs.sent}</h3>
          <span>{sent.total}</span>
        </div>
        <div className="table-scroll table-scroll-rows">
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
              {sent.items.map((entry) => (
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
              {sent.loaded && sent.items.length === 0 && (
                <tr>
                  <td colSpan={7}>{t.networkLogs.empty}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <Pagination
          page={sent.page}
          pageSize={PAGE_SIZE}
          total={sent.total}
          onChange={sent.setPage}
        />
      </section>
    </AppShell>
  );
}
