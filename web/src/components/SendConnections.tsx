import { PlugZap, Radio, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { api } from "../api/client";
import type { SendConnection, SendConnectionList } from "../api/types";
import { ErrorBox } from "./common";
import { useI18n } from "../i18n";

const EMPTY_FORM = {
  name: "",
  host: "",
  port: "11112",
  ae_title: "",
  calling_ae_title: "TORAX_AI",
};

/**
 * Where finished reports are stored. This is the one part of the settings page
 * that is not a description of the deployment: the destination used to be an
 * environment variable, so a site could reach exactly one node.
 */
export function SendConnections() {
  const { t, format } = useI18n();

  const [data, setData] = useState<SendConnectionList | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [probe, setProbe] = useState<Record<string, { ok: boolean; message: string }>>({});

  const load = useCallback(async () => {
    try {
      setError("");
      setData(await api.listDestinations());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      setError("");
      await action();
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const add = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      await api.createDestination({
        name: form.name,
        host: form.host,
        port: Number(form.port) || 11112,
        ae_title: form.ae_title,
        calling_ae_title: form.calling_ae_title,
      });
      setForm(EMPTY_FORM);
    });
  };

  const test = (connection: SendConnection) => {
    setBusy(true);
    api
      .testDestination(connection.id)
      .then((result) => setProbe((current) => ({ ...current, [connection.id]: result })))
      .catch((caught: unknown) =>
        setProbe((current) => ({
          ...current,
          [connection.id]: {
            ok: false,
            message: caught instanceof Error ? caught.message : String(caught),
          },
        })),
      )
      .finally(() => setBusy(false));
  };

  const editable = data?.editable ?? false;
  const field = (key: keyof typeof EMPTY_FORM, value: string) =>
    setForm((current) => ({ ...current, [key]: value }));

  return (
    <section className="panel">
      <div className="panel-title">
        <h3>{t.connections.title}</h3>
        <span>{t.connections.subtitle}</span>
      </div>

      {error && (
        <ErrorBox title={t.connections.title} message={error} onRetry={() => void load()} />
      )}

      {data?.environment && (
        <p className="settings-note settings-note-amber">
          <Radio size={14} aria-hidden />
          {format(t.connections.environmentInUse, {
            address: `${data.environment.ae_title}@${data.environment.host}:${data.environment.port}`,
          })}
        </p>
      )}

      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>{t.connections.name}</th>
              <th>{t.connections.address}</th>
              <th>{t.connections.aeTitle}</th>
              <th>{t.connections.callingAeTitle}</th>
              <th>{t.connections.state}</th>
              <th>{t.connections.automatic}</th>
              {editable && <th />}
            </tr>
          </thead>
          <tbody>
            {(data?.items ?? []).map((connection) => {
              const answered = probe[connection.id];
              return (
                <tr key={connection.id}>
                  <td>
                    {connection.name}
                    {answered && (
                      <small className={answered.ok ? "mono" : "mono probe-failed"}>
                        {" · "}
                        {answered.ok ? t.connections.probeOk : answered.message}
                      </small>
                    )}
                  </td>
                  <td className="mono">{`${connection.host}:${connection.port}`}</td>
                  <td className="mono">{connection.ae_title}</td>
                  <td className="mono">{connection.calling_ae_title}</td>
                  <td>
                    <button
                      type="button"
                      className={`pill ${connection.active ? "pill-completed" : "pill-rejected"}`}
                      disabled={!editable || busy}
                      onClick={() =>
                        void run(() =>
                          api.updateDestination(connection.id, {
                            active: !connection.active,
                          }),
                        )
                      }
                    >
                      {connection.active ? t.common.active : t.common.inactive}
                    </button>
                  </td>
                  <td>
                    <button
                      type="button"
                      className={`pill ${connection.auto_send ? "pill-completed" : "pill-queued"}`}
                      disabled={!editable || busy}
                      onClick={() =>
                        void run(() =>
                          api.updateDestination(connection.id, {
                            auto_send: !connection.auto_send,
                          }),
                        )
                      }
                    >
                      {connection.auto_send
                        ? t.connections.automaticOn
                        : t.connections.automaticOff}
                    </button>
                  </td>
                  {editable && (
                    <td className="row-actions">
                      <button
                        type="button"
                        className="btn btn-subtle"
                        disabled={busy}
                        onClick={() => test(connection)}
                      >
                        <PlugZap size={14} aria-hidden /> {t.connections.test}
                      </button>
                      <button
                        type="button"
                        className="btn btn-danger"
                        disabled={busy}
                        onClick={() => {
                          if (
                            window.confirm(
                              format(t.connections.confirmDelete, {
                                name: connection.name,
                              }),
                            )
                          ) {
                            void run(() => api.deleteDestination(connection.id));
                          }
                        }}
                      >
                        <Trash2 size={14} aria-hidden /> {t.common.remove}
                      </button>
                    </td>
                  )}
                </tr>
              );
            })}
            {data && data.items.length === 0 && (
              <tr>
                <td colSpan={editable ? 7 : 6}>{t.connections.empty}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {editable && (
        <form className="toolbar" onSubmit={add}>
          <input
            className="input"
            required
            value={form.name}
            placeholder={t.connections.namePlaceholder}
            onChange={(event) => field("name", event.target.value)}
          />
          <input
            className="input"
            required
            value={form.host}
            placeholder={t.connections.hostPlaceholder}
            onChange={(event) => field("host", event.target.value)}
          />
          <input
            className="input input-narrow"
            required
            inputMode="numeric"
            value={form.port}
            placeholder={t.connections.portPlaceholder}
            onChange={(event) => field("port", event.target.value)}
          />
          <input
            className="input input-narrow"
            required
            maxLength={16}
            value={form.ae_title}
            placeholder={t.connections.aeTitlePlaceholder}
            onChange={(event) => field("ae_title", event.target.value)}
          />
          <input
            className="input input-narrow"
            maxLength={16}
            value={form.calling_ae_title}
            placeholder={t.connections.callingAeTitlePlaceholder}
            onChange={(event) => field("calling_ae_title", event.target.value)}
          />
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {t.common.add}
          </button>
        </form>
      )}

      <p className="settings-note settings-note-green">
        <Radio size={14} aria-hidden />
        {t.connections.automaticNote}
      </p>
    </section>
  );
}
