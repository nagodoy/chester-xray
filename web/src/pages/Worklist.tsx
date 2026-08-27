import {
  Activity,
  Check,
  CheckSquare,
  ChevronRight,
  ClipboardList,
  CloudUpload,
  Filter,
  RotateCcw,
  Search,
  Square,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearch } from "wouter";

import { api } from "../api/client";
import type { Study, StudyList, StudyStatus } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { AppShell, PageHeading } from "../components/AppShell";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorBox, Skeleton, StatusPill, Thumbnail } from "../components/common";
import { useI18n } from "../i18n";
import type { Dictionary } from "../i18n";
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

/**
 * What to say when a study has no findings to show.
 *
 * An empty list is three different situations, and calling all of them
 * "awaiting model" makes a finished study look stuck: it is what a study
 * still in the queue looks like, what a completed study whose scores all sat
 * under their thresholds looks like, and what one that never reached the
 * model looks like. Only the first is actually waiting.
 */
const emptyFindings = (status: StudyStatus, t: Dictionary): string => {
  if (ACTIVE_STATUSES.includes(status)) return t.worklist.awaitingModel;
  if (status === "completed") return t.worklist.noFindings;
  return t.worklist.notAnalysed;
};

function StudyRow({
  study,
  selectable,
  selected,
  onToggle,
  onDelete,
}: {
  study: Study;
  selectable: boolean;
  selected: boolean;
  onToggle: () => void;
  onDelete: (() => void) | null;
}) {
  const { t } = useI18n();
  const label = study.patient_id ?? t.worklist.unidentified;

  const card = (
    <>
      <Thumbnail url={study.thumbnail_url} alt={t.worklist.headers.image} />
      <div className="study-primary">
        <strong>{label}</strong>
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
          <span className="finding muted">{emptyFindings(study.status, t)}</span>
        )}
      </div>
      <StatusPill value={study.status} />
      <ChevronRight size={15} aria-hidden />
    </>
  );

  return (
    <div className="study-row">
      {selectable && (
        <button
          type="button"
          className="study-select"
          aria-pressed={selected}
          aria-label={`${t.worklist.selectStudy}: ${label}`}
          onClick={onToggle}
        >
          {selected ? <CheckSquare size={18} /> : <Square size={18} />}
        </button>
      )}
      {selectable ? (
        <div className="study-card" role="presentation" onClick={onToggle}>
          {card}
        </div>
      ) : (
        <Link href={`/studies/${study.id}`} className="study-card">
          {card}
        </Link>
      )}
      {onDelete && (
        <button
          type="button"
          className="study-delete"
          aria-label={`${t.worklist.deleteStudy}: ${label}`}
          title={t.worklist.deleteStudy}
          onClick={onDelete}
        >
          <Trash2 size={15} />
        </button>
      )}
    </div>
  );
}

export function Worklist() {
  const { access, can } = useAuth();
  const { t, format } = useI18n();
  const search = useSearch();

  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [data, setData] = useState<StudyList | null>(null);
  const [error, setError] = useState("");
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Either one study, or the whole selection.
  const [pending, setPending] = useState<Study | "selection" | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

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
  const canDelete = Boolean(access?.is_admin);

  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const leaveSelectMode = () => {
    setSelectMode(false);
    setSelected(new Set());
  };

  const runDelete = async () => {
    if (pending === null) return;
    setDeleting(true);
    setDeleteError("");
    try {
      if (pending === "selection") {
        const outcome = await api.bulkDeleteStudies([...selected]);
        const failed = outcome.errors.length + outcome.not_found.length;
        // A batch reports per id, so a partial failure has to be said out loud
        // rather than left to look like a clean sweep.
        if (failed > 0) {
          setDeleteError(
            format(t.worklist.deletePartial, { deleted: outcome.deleted.length, failed }),
          );
        }
        leaveSelectMode();
      } else {
        await api.deleteStudy(pending.id);
      }
      setPending(null);
      await load();
    } catch (caught) {
      setDeleteError(
        format(t.worklist.deleteFailed, {
          error: caught instanceof Error ? caught.message : String(caught),
        }),
      );
      setPending(null);
    } finally {
      setDeleting(false);
    }
  };

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
            ["total", ClipboardList, total, t.worklist.total],
            ["progress", Activity, inProgress, t.worklist.inProgress],
            ["done", Check, completed, t.worklist.completed],
            ["attention", Filter, attention, t.worklist.attention],
            ["failed", XCircle, failed, t.worklist.failed],
          ] as const
        ).map(([tone, Icon, value, label]) => (
          <div className={`stat stat-${tone}`} key={label}>
            <span className="stat-icon" aria-hidden>
              <Icon size={16} />
            </span>
            <div className="stat-body">
              <b>{data ? value : t.common.none}</b>
              <span>{label}</span>
            </div>
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
        <span className="list-summary-right">
          {hasFilters && <small>{t.worklist.globalNote}</small>}
          {canDelete && items.length > 0 && (
            <button
              type="button"
              className={selectMode ? "btn btn-subtle is-active" : "btn btn-subtle"}
              onClick={() => (selectMode ? leaveSelectMode() : setSelectMode(true))}
            >
              {selectMode ? <X size={14} aria-hidden /> : <CheckSquare size={14} aria-hidden />}
              {selectMode ? t.worklist.cancelSelection : t.worklist.select}
            </button>
          )}
        </span>
      </div>

      {selectMode && (
        <div className="selection-bar">
          <strong>{format(t.worklist.selectedCount, { count: selected.size })}</strong>
          <button
            type="button"
            className="link-button"
            onClick={() => setSelected(new Set(items.map((study) => study.id)))}
          >
            {t.worklist.selectAll}
          </button>
          <button
            type="button"
            className="link-button"
            onClick={() => setSelected(new Set())}
          >
            {t.worklist.clearSelection}
          </button>
          <button
            type="button"
            className="btn btn-danger-strong"
            disabled={selected.size === 0}
            onClick={() => setPending("selection")}
          >
            <Trash2 size={14} aria-hidden />
            {format(t.worklist.deleteSelected, { count: selected.size })}
          </button>
        </div>
      )}

      {deleteError && (
        <div className="notice notice-error" role="alert">
          {deleteError}
        </div>
      )}

      {error ? (
        <ErrorBox title={t.worklist.unavailable} message={error} onRetry={() => void load()} />
      ) : !data ? (
        <Skeleton />
      ) : items.length > 0 ? (
        <>
          <div
            className={selectMode ? "worklist-head is-selecting" : "worklist-head"}
            aria-hidden
          >
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
              <StudyRow
                key={study.id}
                study={study}
                selectable={selectMode}
                selected={selected.has(study.id)}
                onToggle={() => toggle(study.id)}
                onDelete={canDelete && !selectMode ? () => setPending(study) : null}
              />
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

      {pending !== null && (
        <ConfirmDialog
          title={t.worklist.deleteTitle}
          body={
            pending === "selection"
              ? format(t.worklist.deleteManyBody, { count: selected.size })
              : format(t.worklist.deleteOneBody, {
                  label: pending.patient_id ?? t.worklist.unidentified,
                })
          }
          confirmLabel={
            pending === "selection"
              ? format(t.worklist.deleteSelected, { count: selected.size })
              : t.worklist.deleteStudy
          }
          busy={deleting}
          onConfirm={() => void runDelete()}
          onCancel={() => setPending(null)}
        />
      )}
    </AppShell>
  );
}
