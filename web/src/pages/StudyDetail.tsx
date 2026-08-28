import { ArrowLeft, Check, Send, X } from "lucide-react";
import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "wouter";

import { api } from "../api/client";
import type { AnalysisResult, StudyDetail as StudyDetailType } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { AppShell } from "../components/AppShell";
import { ErrorBox, Notice, Panel, Skeleton, StatusPill, Thumbnail } from "../components/common";
import { useI18n } from "../i18n";

// Charting is a large dependency reachable only from this screen.
const ScoreChart = lazy(() => import("../components/ScoreChart"));

const REVIEWER_ROLES = new Set(["admin", "radiologist", "radiology_validator"]);

interface Row {
  pathology: string;
  raw: number;
  normalized: number;
  threshold: number;
  above: boolean;
}

/** Flatten the most recent result into rows, tolerating partially populated maps. */
const latestRows = (results: AnalysisResult[]): Row[] => {
  const latest = [...results].sort((a, b) => a.created_at.localeCompare(b.created_at)).at(-1);
  if (!latest?.raw_scores) return [];

  const names = new Set([
    ...Object.keys(latest.raw_scores),
    ...Object.keys(latest.op_normalized_scores ?? {}),
    ...Object.keys(latest.thresholds ?? {}),
  ]);

  return [...names].map((pathology) => ({
    pathology,
    raw: latest.raw_scores?.[pathology] ?? 0,
    normalized: latest.op_normalized_scores?.[pathology] ?? 0,
    threshold: latest.thresholds?.[pathology] ?? 0,
    above:
      latest.above_threshold?.[pathology] ??
      (latest.above_threshold_findings ?? []).includes(pathology),
  }));
};

export function StudyDetail() {
  const { id } = useParams<{ id: string }>();
  const { access } = useAuth();
  const { t, format } = useI18n();

  const [study, setStudy] = useState<StudyDetailType | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // A delivery outcome is reported beside the study, not by replacing it: the
  // study loaded fine, and a destination that refused is ordinary news.
  const [delivery, setDelivery] = useState<{ ok: boolean; detail: string } | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      setError("");
      setStudy(await api.getStudy(id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const send = async () => {
    if (!id) return;
    setBusy(true);
    setDelivery(null);
    try {
      setStudy(await api.sendReport(id));
      setDelivery({ ok: true, detail: "" });
    } catch (caught) {
      setDelivery({
        ok: false,
        detail: caught instanceof Error ? caught.message : String(caught),
      });
    } finally {
      setBusy(false);
    }
  };

  const rows = useMemo(() => latestRows(study?.results ?? []), [study]);
  const chartData = rows.map((row) => ({ name: row.pathology, score: row.normalized }));

  if (error) {
    return (
      <AppShell>
        <ErrorBox title={t.detail.unavailable} message={error} onRetry={() => void load()} />
      </AppShell>
    );
  }

  if (!study || !id) {
    return (
      <AppShell>
        <Skeleton count={2} />
      </AppShell>
    );
  }

  const canReview =
    study.status === "needs_review" &&
    Boolean(access && REVIEWER_ROLES.has(access.role));

  return (
    <AppShell>
      <div className="page-hero grid-pattern">
        <Link href="/worklist" className="eyebrow back-link">
          <ArrowLeft size={14} aria-hidden /> {t.detail.back}
        </Link>

        <div className="detail-header">
          <div>
            <h1>{study.description ?? t.detail.defaultTitle}</h1>
            <div className="meta-row">
              <span className="mono">{study.id}</span>
              <StatusPill value={study.status} />
              <span>{study.source ?? t.worklist.manualUpload}</span>
            </div>
          </div>
          <div className="detail-actions">
            {study.status === "error" && (
              <button
                type="button"
                className="btn btn-subtle"
                disabled={busy}
                onClick={() => void act(() => api.retryStudy(id))}
              >
                {t.detail.retryAnalysis}
              </button>
            )}
            {study.status === "completed" && (
              <button
                type="button"
                className="btn btn-subtle"
                disabled={busy}
                onClick={() => void send()}
              >
                <Send size={15} aria-hidden /> {busy ? t.detail.sending : t.detail.sendReport}
              </button>
            )}
            {canReview && (
              <>
                <button
                  type="button"
                  className="btn btn-accent"
                  disabled={busy}
                  onClick={() => void act(() => api.reviewStudy(id, "approve"))}
                >
                  <Check size={15} aria-hidden /> {t.detail.approve}
                </button>
                <button
                  type="button"
                  className="btn btn-danger"
                  disabled={busy}
                  onClick={() => void act(() => api.reviewStudy(id, "reject"))}
                >
                  <X size={15} aria-hidden /> {t.detail.reject}
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {delivery && (
        <Notice strong={delivery.ok ? t.detail.reportSent : t.detail.reportNotSent}>
          {delivery.detail}
        </Notice>
      )}
      {study.status === "error" && (
        <Notice strong={t.detail.inferenceError}>
          {study.error_message ?? t.detail.inferenceErrorBody}
        </Notice>
      )}
      {study.status === "needs_review" && (
        <Notice strong={t.detail.reviewRequired}>{t.detail.reviewRequiredBody}</Notice>
      )}

      <div className="detail-grid">
        <div className="detail-media">
          <Panel title={t.detail.image} aside={<span>{study.modality ?? "XR"}</span>}>
            <div className="detail-image">
              <Thumbnail url={study.thumbnail_url} alt={t.detail.image} />
            </div>
          </Panel>

          <Panel title={t.detail.metadata} aside={<span>{t.detail.sourceRecord}</span>}>
            <dl className="metadata">
              {(
                [
                  [t.detail.patient, study.patient_id ?? t.worklist.unidentified],
                  [
                    t.detail.ageSex,
                    `${study.patient_age ?? t.common.none} / ${study.patient_sex ?? t.common.none}`,
                  ],
                  [t.detail.studyDate, study.study_date ?? t.common.none],
                  [t.detail.viewPosition, study.view_position ?? t.common.none],
                  [t.detail.modelVersion, study.model_version ?? t.common.none],
                  [t.detail.preprocessing, study.preprocessing_version ?? t.common.none],
                ] as const
              ).map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </Panel>
        </div>

        <div>
          <Panel
            title={t.detail.findings}
            aside={<span>{format(t.detail.outputs, { count: rows.length })}</span>}
          >
            <Notice strong={t.detail.notProbability}>{t.detail.notProbabilityBody}</Notice>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>{t.detail.pathology}</th>
                    <th>{t.detail.rawOutput}</th>
                    <th>{t.detail.normalized}</th>
                    <th>{t.detail.threshold}</th>
                    <th>{t.detail.flag}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.pathology}>
                      <td>
                        <b>{row.pathology}</b>
                      </td>
                      <td className="mono">{row.raw.toFixed(4)}</td>
                      <td>
                        <div className="bar-cell">
                          <div className="bar">
                            <i style={{ width: `${Math.min(100, row.normalized * 100)}%` }} />
                          </div>
                          <span className="mono">{row.normalized.toFixed(3)}</span>
                        </div>
                      </td>
                      <td className="mono">{row.threshold.toFixed(3)}</td>
                      <td>
                        <span className={row.above ? "pill pill-needs_review" : "pill pill-completed"}>
                          {row.above ? t.detail.above : t.detail.below}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          {rows.length > 0 && (
            <Panel
              title={t.detail.distribution}
              aside={<span>{t.detail.normalizedScore}</span>}
            >
              <div className="chart">
                <Suspense fallback={<div className="skeleton" />}>
                  <ScoreChart data={chartData} />
                </Suspense>
              </div>
            </Panel>
          )}
        </div>
      </div>
    </AppShell>
  );
}
