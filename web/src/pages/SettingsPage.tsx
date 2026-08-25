import { Globe2, LockKeyhole, RadioTower, Server, ShieldCheck, Wifi } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type { DicomwebSettings } from "../api/types";
import { AppShell, PageHeading } from "../components/AppShell";
import { ErrorBox, Skeleton } from "../components/common";
import { useI18n } from "../i18n";

function Row({ label, value, mono = false }: { label: string; value: string | number; mono?: boolean }) {
  return (
    <div className="setting-row">
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{value}</dd>
    </div>
  );
}

function ConnectionStatus({ status, label }: { status: string; label: string }) {
  return (
    <span className={`connection-status connection-status-${status}`}>
      <i aria-hidden />
      {label}
    </span>
  );
}

export function SettingsPage() {
  const { t } = useI18n();
  const [data, setData] = useState<DicomwebSettings | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setError("");
      setData(await api.getSettings());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const anonymous = data?.wado_anonymous ?? false;

  return (
    <AppShell>
      <PageHeading
        eyebrow={t.settings.eyebrow}
        title={anonymous ? t.settings.titleAnonymous : t.settings.title}
        subtitle={anonymous ? t.settings.subtitleAnonymous : t.settings.subtitle}
        actions={
          <div className="settings-mark">
            <RadioTower size={18} aria-hidden />
            <span>{t.settings.readOnly}</span>
          </div>
        }
      />

      {error ? (
        <ErrorBox title={t.settings.unavailable} message={error} onRetry={() => void load()} />
      ) : !data ? (
        <Skeleton />
      ) : (
        <div className="settings-grid">
          <section className="settings-card">
            <div className="settings-card-header">
              <div>
                <div className="settings-kicker settings-kicker-green">
                  <span className="settings-dot" aria-hidden />
                  DICOM SCP <span className="settings-kicker-muted">(C-STORE)</span>
                </div>
                <h2>{t.settings.scpTitle}</h2>
              </div>
              <ConnectionStatus status={data.scp.status} label={data.scp.status_label} />
            </div>
            <p className="settings-description">{t.settings.scpDescription}</p>
            <dl className="settings-rows">
              <Row label={t.settings.aeTitle} value={data.scp.ae_title} mono />
              <Row label={t.settings.port} value={data.scp.port} mono />
              <Row label={t.settings.services} value={data.scp.services.join(" / ")} mono />
              <Row label={t.settings.transport} value={data.scp.transport} />
            </dl>
            <div className="settings-inset">
              <div className="settings-inset-title">
                <Server size={15} aria-hidden />
                {t.settings.externalGateway}
              </div>
              <dl className="settings-rows">
                <Row label={t.settings.address} value={data.scp.host} mono />
                <Row label={t.settings.stowTarget} value={data.scp.gateway_target} mono />
                <Row
                  label={t.settings.worklistOwner}
                  value={data.scp.owner_configured ? t.settings.defined : t.settings.pending}
                />
              </dl>
            </div>
            <p className="settings-note settings-note-green">
              <RadioTower size={14} aria-hidden />
              {t.settings.scpNote}
            </p>
          </section>

          <section className="settings-card">
            <div className="settings-card-header">
              <div>
                <div className="settings-kicker settings-kicker-blue">
                  <span className="settings-dot" aria-hidden />
                  {anonymous ? "WADO STOW-RS" : "DICOMweb STOW-RS"}
                </div>
                <h2>{t.settings.stowTitle}</h2>
              </div>
              <ConnectionStatus status={data.stow_rs.status} label={data.stow_rs.status_label} />
            </div>
            <p className="settings-description">
              {anonymous ? t.settings.stowDescriptionAnonymous : t.settings.stowDescription}
            </p>
            <dl className="settings-rows">
              <Row label={t.settings.url} value={data.stow_rs.url} mono />
              <Row label={t.settings.aeTitle} value={data.stow_rs.ae_title} />
              <Row
                label={t.settings.encryption}
                value={data.stow_rs.https ? "HTTPS" : "HTTP"}
                mono
              />
              <Row label={t.settings.services} value={data.stow_rs.services.join(" / ")} mono />
            </dl>
            <div className="settings-inset">
              <div className="settings-inset-title">
                <Globe2 size={15} aria-hidden />
                {t.settings.endpointDetails}
              </div>
              <dl className="settings-rows">
                <Row label={t.settings.hostname} value={data.stow_rs.hostname} mono />
                <Row label={t.settings.path} value={data.stow_rs.path} mono />
                <Row label={t.settings.port} value={data.stow_rs.port} mono />
                <Row
                  label="HTTPS"
                  value={data.stow_rs.https ? t.settings.httpsActive : t.settings.httpsInactive}
                />
              </dl>
            </div>
            <p className="settings-note settings-note-amber">
              <LockKeyhole size={14} aria-hidden />
              {data.stow_rs.request_limit}
            </p>
          </section>

          <section className="settings-card">
            <div className="settings-card-header">
              <div>
                <div className="settings-kicker settings-kicker-purple">
                  <span className="settings-dot" aria-hidden />
                  {t.settings.credentialsKicker}
                </div>
                <h2>{t.settings.credentialsTitle}</h2>
              </div>
              <ConnectionStatus
                status={
                  anonymous || data.service_token_configured ? "configured" : "not_configured"
                }
                label={
                  anonymous
                    ? t.settings.anonymousLabel
                    : data.service_token_configured
                      ? t.settings.configured
                      : t.settings.notConfigured
                }
              />
            </div>
            <div className="security-content">
              <div>
                <p className="settings-description">
                  {anonymous
                    ? t.settings.credentialsDescriptionAnonymous
                    : t.settings.credentialsDescription}
                </p>
                {!anonymous && (
                  <div className="credential-methods">
                    <span>
                      <LockKeyhole size={13} aria-hidden />
                      X-DICOM-Ingest-Key
                    </span>
                    <span>
                      <LockKeyhole size={13} aria-hidden />
                      Authorization: Bearer
                    </span>
                  </div>
                )}
              </div>
              <div className="security-safe">
                <ShieldCheck size={18} aria-hidden />
                <strong>
                  {anonymous ? t.settings.controlledMode : t.settings.secretProtected}
                </strong>
                <span>
                  {anonymous ? t.settings.controlledModeBody : t.settings.secretProtectedBody}
                </span>
              </div>
            </div>
            <p className="settings-note settings-note-purple">
              <Wifi size={14} aria-hidden />
              {t.settings.securityNote}
            </p>
          </section>

          <div className="settings-footer-note">
            <Wifi size={14} aria-hidden />
            <span>{t.settings.footerNote}</span>
          </div>
        </div>
      )}
    </AppShell>
  );
}
