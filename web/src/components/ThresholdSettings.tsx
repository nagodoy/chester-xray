import { RotateCcw, SlidersHorizontal, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type { ThresholdList, ThresholdRow } from "../api/types";
import { ErrorBox } from "./common";
import { useI18n } from "../i18n";

/** Enough digits to show a change the model can actually act on. */
const precise = (value: number) => value.toFixed(6);

/**
 * The operating point each reported output is judged against.
 *
 * This is the one screen in the application that changes what the model decides
 * rather than describing it, so it shows the whole comparison rather than a
 * single editable number: the built-in default, the value in force, and both
 * edges of the doubt band a report actually classifies against. A reader who
 * cannot see the default cannot tell an adjustment from a mistake.
 *
 * Only future analyses are affected. A finished study keeps the points that were
 * in force when it ran, which is why the note under the table says so.
 */
export function ThresholdSettings() {
  const { t, format } = useI18n();

  const [data, setData] = useState<ThresholdList | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Which row is being edited, and the text in its field. Kept as a string so a
  // half-typed number is not coerced to something the field then displays back.
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const load = useCallback(async () => {
    try {
      setError("");
      setData(await api.listThresholds());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (action: () => Promise<ThresholdList>) => {
    setBusy(true);
    try {
      setError("");
      setData(await action());
      setEditing(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const save = (row: ThresholdRow) => {
    const value = Number(draft.replace(",", "."));
    if (!Number.isFinite(value)) {
      setError(t.thresholds.notANumber);
      return;
    }
    if (value < row.minimum || value > row.maximum) {
      setError(
        format(t.thresholds.outOfRange, {
          pathology: row.pathology,
          minimum: precise(row.minimum),
          maximum: precise(row.maximum),
        }),
      );
      return;
    }
    void run(() => api.setThreshold(row.pathology, value));
  };

  const editable = data?.editable ?? false;
  const band = data ? Math.round(data.doubt_band * 100) : 0;
  const changed = (data?.items ?? []).filter((row) => row.overridden).length;

  return (
    <section className="panel">
      <div className="panel-title">
        <h3>{t.thresholds.title}</h3>
        <span>{format(t.thresholds.subtitle, { band: String(band) })}</span>
      </div>

      {error && (
        <ErrorBox title={t.thresholds.title} message={error} onRetry={() => void load()} />
      )}

      {changed > 0 && (
        <p className="settings-note settings-note-amber">
          <TriangleAlert size={14} aria-hidden />
          {format(t.thresholds.overriddenNotice, { count: String(changed) })}
        </p>
      )}

      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>{t.thresholds.pathology}</th>
              <th>{t.thresholds.default}</th>
              <th>{t.thresholds.effective}</th>
              <th>{t.thresholds.lower}</th>
              <th>{t.thresholds.upper}</th>
              <th>{t.thresholds.origin}</th>
              {editable && <th />}
            </tr>
          </thead>
          <tbody>
            {(data?.items ?? []).map((row) => (
              <tr key={row.pathology}>
                <td>{row.pathology}</td>
                <td className="mono">{precise(row.default)}</td>
                <td className="mono">
                  {editing === row.pathology ? (
                    <input
                      className="input input-narrow"
                      autoFocus
                      inputMode="decimal"
                      value={draft}
                      disabled={busy}
                      onChange={(event) => setDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") save(row);
                        if (event.key === "Escape") setEditing(null);
                      }}
                      aria-label={format(t.thresholds.fieldLabel, {
                        pathology: row.pathology,
                      })}
                    />
                  ) : (
                    precise(row.effective)
                  )}
                </td>
                <td className="mono">{precise(row.lower)}</td>
                <td className="mono">{precise(row.upper)}</td>
                <td>
                  {row.overridden ? (
                    <span className="pill pill-needs_review" title={row.updated_by ?? undefined}>
                      {format(t.thresholds.adjusted, {
                        factor: (row.factor ?? 1).toFixed(2),
                      })}
                    </span>
                  ) : (
                    <span className="pill pill-completed">{t.thresholds.builtIn}</span>
                  )}
                </td>
                {editable && (
                  <td className="row-actions">
                    {editing === row.pathology ? (
                      <>
                        <button
                          type="button"
                          className="btn btn-primary"
                          disabled={busy}
                          onClick={() => save(row)}
                        >
                          {t.common.save}
                        </button>
                        <button
                          type="button"
                          className="btn btn-subtle"
                          disabled={busy}
                          onClick={() => setEditing(null)}
                        >
                          {t.common.cancel}
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="btn btn-subtle"
                          disabled={busy}
                          onClick={() => {
                            setError("");
                            setEditing(row.pathology);
                            setDraft(precise(row.effective));
                          }}
                        >
                          <SlidersHorizontal size={14} aria-hidden /> {t.thresholds.adjust}
                        </button>
                        <button
                          type="button"
                          className="btn btn-danger"
                          disabled={busy || !row.overridden}
                          onClick={() => {
                            if (
                              window.confirm(
                                format(t.thresholds.confirmReset, {
                                  pathology: row.pathology,
                                  value: precise(row.default),
                                }),
                              )
                            ) {
                              void run(() => api.resetThreshold(row.pathology));
                            }
                          }}
                        >
                          <RotateCcw size={14} aria-hidden /> {t.thresholds.reset}
                        </button>
                      </>
                    )}
                  </td>
                )}
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr>
                <td colSpan={editable ? 7 : 6}>{t.thresholds.empty}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <p className="settings-footer-note">
        {editable ? t.thresholds.footerAdmin : t.thresholds.footerReadOnly}
      </p>
    </section>
  );
}
