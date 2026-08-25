import { CloudUpload, FileImage, UploadCloud } from "lucide-react";
import { useState } from "react";
import type { DragEvent } from "react";

import { api } from "../api/client";
import type { UploadOutcome } from "../api/types";
import { useI18n } from "../i18n";

const MEGABYTE = 1024 * 1024;

export function UploadPanel({ onDone }: { onDone: () => void }) {
  const { t, format } = useI18n();
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [outcome, setOutcome] = useState<UploadOutcome | null>(null);

  const choose = (list: FileList | null) => {
    setFiles(list ? Array.from(list) : []);
    setOutcome(null);
    setError("");
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    choose(event.dataTransfer.files);
  };

  const submit = async () => {
    if (files.length === 0 || busy) return;
    setBusy(true);
    setError("");
    setOutcome(null);
    try {
      const result = await api.upload(files);
      setOutcome(result);
      // Only close automatically when nothing needs the operator's attention.
      if (result.errors.length === 0) onDone();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel upload-panel">
      <div className="panel-title">
        <div>
          <h3>{t.upload.title}</h3>
          <span>{t.upload.testDataOnly}</span>
        </div>
      </div>

      <div className="upload-zone" onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
        <UploadCloud size={26} aria-hidden />
        <p>{t.upload.dropHint}</p>
        <input
          id="upload-files"
          type="file"
          multiple
          hidden
          accept=".dcm,.dicom,image/png,image/jpeg"
          onChange={(event) => choose(event.target.files)}
        />
        <label className="btn btn-subtle" htmlFor="upload-files">
          <CloudUpload size={15} aria-hidden />
          {t.upload.choose}
        </label>
      </div>

      {files.length > 0 && (
        <div className="upload-selection">
          {files.map((file) => (
            <div key={`${file.name}-${file.size}`} className="meta-row">
              <FileImage size={14} aria-hidden />
              {file.name}
              <span className="mono">
                {(file.size / MEGABYTE).toFixed(1)} MB ·{" "}
                {busy ? t.upload.sending : t.upload.ready}
              </span>
            </div>
          ))}
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => void submit()}
          >
            {busy ? t.upload.sending : t.upload.submit}
          </button>
        </div>
      )}

      {outcome && outcome.errors.length > 0 && (
        <div className="notice upload-result" role="alert">
          <strong>
            {format(t.upload.summary, {
              accepted: outcome.studies.length,
              rejected: outcome.errors.length,
            })}
          </strong>
          {outcome.errors.map((item) => (
            <div key={item.filename} className="meta-row">
              <span>{item.filename}</span>
              <span>{item.error}</span>
            </div>
          ))}
          <button type="button" className="btn btn-subtle" onClick={onDone}>
            {t.upload.back}
          </button>
        </div>
      )}

      {error && (
        <div className="error-box" role="alert">
          {error}
        </div>
      )}
    </section>
  );
}
