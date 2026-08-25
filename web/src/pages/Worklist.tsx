import {
  Activity,
  Check,
  ChevronRight,
  ClipboardList,
  CloudUpload,
  Filter,
  RotateCcw,
  Search,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearch } from "wouter";

import { api } from "../api/client";
import type { Study, StudyList, StudyStatus } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { AppShell, PageHeading } from "../components/AppShell";
import { ErrorBox, Skeleton, StatusPill, Thumbnail } from "../components/common";
import { useI18n } from "../i18n";
import { validationReason } from "../i18n/validation";
import { UploadPanel } from "./UploadPanel";

const ACTIVE_STATUSES: StudyStatus[] = ["received", "validating", "queued", "processing"];
const POLL_INTERVAL_MS = 4000;
const ALL_STATUSES: StudyStatus[] = [
  "received",
  "validating",
  "queued",
  "processing",
  "completed",
  "needs_review",
  "rejected",
  "error",
];

const displayAge = (value: string | null, fallback: string): string => {
  if (!value) return fallback;
  const trimmed = value.trim().toUpperCase();
  // DICOM ages are like "045Y"; anything else is passed through as years.
  return /^\d{3}[DWMY]$/.test(trimmed) ? trimmed : `${value}y`;
};

function StudyRow({ study }: { study: Study }) {
  const { t } = useI18n();
  return (
    <Link href={`/studies/${study.id}`} className="study-card">
      <Thumbnail url={study.thumbnail_url} alt={t.worklist.headers.image} />
      <div className="study-primary">
        <strong>{study.patient_id ?? t.worklist.unidentified}</strong>
        <div className="meta-row">
          {study.description ?? t.worklist.defaultDescription} · {study.modality ?? "XR"}
          {study.view_position ? ` · ${study.view_position}` : ""}
        </div>
      </div>
      <div className="study-cell">
        <b>
          {displayAge(study.patient_age, t.common.none)} / {study.patient_sex ?? t.common.none}
        </b>
        <span className="mono">{study.study_date ?? t.common.none}</span>
      </div>
      <div className="study-cell">
        <b>{study.source ?? t.worklist.manualUpload}</b>
        <span>{validationReason(study, t) || t.worklist.pendingValidation}</span>
      </div>
      <div className="findings">
        {study.top_findings.length > 0 ? (
          study.top_findings.slice(0, 2).map((finding) => (
            <span className="finding" key={finding.pathology}>
              {finding.pathology} {(finding.normalized_score ?? 0).toFixed(2)}
            </span>
          ))
        ) : (
          <span className="finding muted">{t.worklist.awaitingModel}</span>
        )}
      </div>
      <StatusPill value={study.status} />
      <ChevronRight size={15} aria-hidden />
    </Link>
  );
}

export function Worklist() {
  const { can } = useAuth();
  const { t, format } = useI18n();
  const search = useSearch();

  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [data, setData] = useState<StudyList | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setStatus(new URLSearchParams(search).get("status") ?? "");
  }, [search]);

  const load = useCallback(async () => {
    try {
      setError("");
      setData(await api.listStudies({ search: query, status }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [query, status]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll only while something is actually in flight.
  const hasActiveWork = useMemo(
    () => (data?.items ?? []).some((study) => ACTIVE_STATUSES.includes(study.status)),
    [data],
  );

  useEffect(() => {
    if (!hasActiveWork) return;
    const timer = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [hasActiveWork, load]);

  const counts = data?.counts ?? {};
  const items = data?.items ?? [];
  const inProgress = ACTIVE_STATUSES.reduce((sum, key) => sum + (counts[key] ?? 0), 0);
  const completed = counts.completed ?? 0;
  const attention = (counts.needs_review ?? 0) + (counts.rejected ?? 0);
  const failed = counts.error ?? 0;
  const total = Object.values(counts).reduce<number>((sum, value) => sum + (value ?? 0), 0);
  const completedRate = total > 0 ? Math.round((completed / total) * 100) : 0;
  const attentionRate = total > 0 ? Math.round((attention / total) * 100) : 0;
  const hasFilters = Boolean(query || status);

  const ring = `conic-gradient(var(--teal) 0 ${completedRate}%, var(--amber) ${completedRate}% ${Math.min(
    100,
    completedRate + attentionRate,
  )}%, var(--ring-rest) ${Math.min(100, completedRate + attentionRate)}% 100%)`;

  return (
    <AppShell>
      <PageHeading
        eyebrow={t.worklist.eyebrow}
        title={t.worklist.title}
        subtitle={t.worklist.subtitle}
        actions={
          <>
            <div className="live-indicator">
              <i aria-hidden /> {t.worklist.synced}
            </div>
            {can("upload") && (
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setUploadOpen((open) => !open)}
              >
                <CloudUpload size={15} aria-hidden />
                {uploadOpen ? t.worklist.closeUpload : t.worklist.analyse}
              </button>
            )}
          </>
        }
      />

      {uploadOpen && (
        <UploadPanel
          onDone={() => {
            setUploadOpen(false);
            void load();
          }}
        />
      )}

      <div className="stats">
        {(
          [
            [ClipboardList, total, t.worklist.total],
            [Activity, inProgress, t.worklist.inProgress],
            [Check, completed, t.worklist.completed],
            [Filter, attention, t.worklist.attention],
            [XCircle, failed, t.worklist.failed],
          ] as const
        ).map(([Icon, value, label]) => (
          <div className="stat" key={label}>
            <Icon size={16} aria-hidden />
            <b>{data ? value : t.common.none}</b>
            <span>{label}</span>
          </div>
        ))}
      </div>

      <section className="quality-summary" aria-label={t.worklist.distribution}>
        <div className="summary-title">
          <span className="summary-icon" aria-hidden>
            <Activity size={15} />
          </span>
          <div>
            <strong>{t.worklist.distribution}</strong>
            <small>{t.worklist.distributionNote}</small>
          </div>
        </div>
        <div className="summary-chart">
          <div className="status-ring" style={{ background: ring }}>
            <span>
              {completedRate}%<small>{t.worklist.completedShare}</small>
            </span>
          </div>
          <div className="summary-legend">
            <span>
              <i className="legend-completed" aria-hidden />
              {t.worklist.completed} <b>{completed}</b>
            </span>
            <span>
              <i className="legend-attention" aria-hidden />
              {t.worklist.attention} <b>{attention}</b>
            </span>
            <span>
              <i className="legend-queue" aria-hidden />
              {t.worklist.queueShare} <b>{inProgress}</b>
            </span>
          </div>
        </div>
        <div className="summary-meta">
          <span>{items.length}</span>
          <small>{t.worklist.shown}</small>
        </div>
      </section>

      <section className={filtersOpen ? "filter-panel is-open" : "filter-panel"}>
        <button
          type="button"
          className="filter-heading"
          aria-expanded={filtersOpen}
          onClick={() => setFiltersOpen((open) => !open)}
        >
          <span>
            <Filter size={15} aria-hidden />
            {t.worklist.filters}
          </span>
          <ChevronRight size={15} aria-hidden />
        </button>
        {filtersOpen && (
          <div className="filter-grid">
            <label className="filter-search">
              <span>{t.worklist.search}</span>
              <div className="search">
                <Search size={15} aria-hidden />
                <input
                  className="input"
                  placeholder={t.worklist.searchPlaceholder}
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </div>
            </label>
            <label>
              <span>{t.worklist.statusLabel}</span>
              <select
                className="select"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              >
                <option value="">{t.worklist.allStatuses}</option>
                {ALL_STATUSES.filter(
                  (value) => value !== "needs_review" || can("review"),
                ).map((value) => (
                  <option key={value} value={value}>
                    {t.status[value]}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn btn-subtle"
              disabled={!hasFilters}
              onClick={() => {
                setQuery("");
                setStatus("");
              }}
            >
              <RotateCcw size={14} aria-hidden />
              {t.worklist.clearFilters}
            </button>
          </div>
        )}
      </section>

      <div className="list-summary">
        <span>
          {format(t.worklist.selection, { shown: items.length, total: data?.total ?? 0 })}
        </span>
        {hasFilters && <small>{t.worklist.globalNote}</small>}
      </div>

      {error ? (
        <ErrorBox title={t.worklist.unavailable} message={error} onRetry={() => void load()} />
      ) : !data ? (
        <Skeleton />
      ) : items.length > 0 ? (
        <>
          <div className="worklist-head" aria-hidden>
            <span>{t.worklist.headers.image}</span>
            <span>{t.worklist.headers.study}</span>
            <span>{t.worklist.headers.demographics}</span>
            <span>{t.worklist.headers.origin}</span>
            <span>{t.worklist.headers.findings}</span>
            <span>{t.worklist.headers.status}</span>
            <span />
          </div>
          <div className="study-list">
            {items.map((study) => (
              <StudyRow key={study.id} study={study} />
            ))}
          </div>
        </>
      ) : (
        <div className="empty">
          <ClipboardList size={28} aria-hidden />
          <h3>{t.worklist.emptyTitle}</h3>
          <p>{t.worklist.emptyBody}</p>
        </div>
      )}
    </AppShell>
  );
}
