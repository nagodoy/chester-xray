import { AlertTriangle, FileImage } from "lucide-react";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { api } from "../api/client";
import type { StudyStatus } from "../api/types";
import { useI18n } from "../i18n";

export function StatusPill({ value }: { value: StudyStatus }) {
  const { t } = useI18n();
  return <span className={`pill pill-${value}`}>{t.status[value] ?? value}</span>;
}

export function Skeleton({ count = 3 }: { count?: number }) {
  return (
    <div className="skeleton-stack">
      {Array.from({ length: count }, (_, index) => (
        <div className="skeleton" key={index} />
      ))}
    </div>
  );
}

export function ErrorBox({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry?: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="error-box" role="alert">
      <AlertTriangle size={22} aria-hidden />
      <h3>{title}</h3>
      <p>{message}</p>
      {onRetry && (
        <button type="button" className="btn btn-subtle" onClick={onRetry}>
          {t.common.retry}
        </button>
      )}
    </div>
  );
}

/**
 * Thumbnails need the session header, so they cannot be a plain <img src>. The
 * blob URL is revoked when the component unmounts or the study changes.
 */
export function Thumbnail({ url, alt }: { url: string | null; alt: string }) {
  const [objectUrl, setObjectUrl] = useState<string>("");

  useEffect(() => {
    if (!url) {
      setObjectUrl("");
      return;
    }
    let created = "";
    let cancelled = false;

    api
      .fetchThumbnail(url)
      .then((blob) => {
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      })
      .catch(() => {
        if (!cancelled) setObjectUrl("");
      });

    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [url]);

  return (
    <div className="thumb">
      {objectUrl ? <img src={objectUrl} alt={alt} /> : <FileImage size={28} aria-hidden />}
    </div>
  );
}

export function Panel({
  title,
  aside,
  children,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <div className="panel-title">
        <h3>{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function Notice({ strong, children }: { strong: string; children?: ReactNode }) {
  return (
    <div className="notice">
      <strong>{strong}</strong> {children}
    </div>
  );
}

/**
 * Page controls for a table that reads one window of rows at a time. The page
 * index is zero-based here, as the offset the API wants, and one-based in the
 * label, which is what a reader expects. A single page of results renders
 * nothing: the controls would only ever be disabled.
 */
export function Pagination({
  page,
  pageSize,
  total,
  onChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onChange: (page: number) => void;
}) {
  const { t, format } = useI18n();
  if (total <= pageSize) return null;

  const pageCount = Math.ceil(total / pageSize);
  const first = page * pageSize + 1;
  const last = Math.min(total, (page + 1) * pageSize);

  return (
    <div className="pagination">
      <span className="pagination-range">
        {format(t.common.showingRange, { first, last, total })}
      </span>
      <div className="pagination-controls">
        <button
          type="button"
          className="btn btn-subtle"
          onClick={() => onChange(page - 1)}
          disabled={page <= 0}
        >
          {t.common.previous}
        </button>
        <span className="pagination-page" aria-live="polite">
          {format(t.common.pageOf, { page: page + 1, pages: pageCount })}
        </span>
        <button
          type="button"
          className="btn btn-subtle"
          onClick={() => onChange(page + 1)}
          disabled={page >= pageCount - 1}
        >
          {t.common.next}
        </button>
      </div>
    </div>
  );
}
