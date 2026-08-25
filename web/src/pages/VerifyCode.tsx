import { ArrowLeft, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ClipboardEvent, KeyboardEvent } from "react";

import { api, ApiError } from "../api/client";
import type { Access } from "../api/types";
import { useI18n } from "../i18n";

const CODE_LENGTH = 6;
const RESEND_COOLDOWN_SECONDS = 60;

/** Show enough of the address to recognize it, not enough to disclose it. */
export function maskEmail(email: string): string {
  const at = email.lastIndexOf("@");
  if (at <= 0) return email;
  const local = email.slice(0, at);
  const domain = email.slice(at);
  if (local.length <= 2) return `${local}${domain}`;
  return `${local.slice(0, 2)}${"*".repeat(local.length - 2)}${domain}`;
}

interface Props {
  email: string;
  onVerified: (access: Access) => void;
  onBack: () => void;
}

export function VerifyCode({ email, onVerified, onBack }: Props) {
  const { t, format } = useI18n();
  const [digits, setDigits] = useState<string[]>(() => Array(CODE_LENGTH).fill(""));
  const [busy, setBusy] = useState(false);
  const [resending, setResending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [cooldown, setCooldown] = useState(RESEND_COOLDOWN_SECONDS);
  const inputs = useRef<(HTMLInputElement | null)[]>([]);

  useEffect(() => {
    inputs.current[0]?.focus();
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  const focusInput = (index: number) => {
    inputs.current[Math.max(0, Math.min(index, CODE_LENGTH - 1))]?.focus();
  };

  const reset = () => {
    setDigits(Array(CODE_LENGTH).fill(""));
    window.setTimeout(() => focusInput(0), 0);
  };

  const submit = async (code: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      onVerified(await api.verifyCode(email, code));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
      reset();
    } finally {
      setBusy(false);
    }
  };

  /** Fill from `index` onwards, so a paste and a keystroke share one path. */
  const fill = (index: number, characters: string) => {
    if (!characters) return;
    const next = [...digits];
    for (let offset = 0; offset < characters.length && index + offset < CODE_LENGTH; offset += 1) {
      next[index + offset] = characters[offset] as string;
    }
    setDigits(next);
    setError("");
    focusInput(index + characters.length);
    if (next.every((digit) => digit !== "")) void submit(next.join(""));
  };

  const onChange = (index: number, raw: string) => {
    const cleaned = raw.replace(/\D/g, "");
    // A native autofill drops the whole code into one box.
    fill(index, cleaned.length > 1 ? cleaned : cleaned.slice(-1));
  };

  const onKeyDown = (index: number, event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Backspace") {
      if (digits[index]) {
        const next = [...digits];
        next[index] = "";
        setDigits(next);
        return;
      }
      if (index > 0) {
        event.preventDefault();
        const next = [...digits];
        next[index - 1] = "";
        setDigits(next);
        focusInput(index - 1);
      }
      return;
    }
    if (event.key === "ArrowLeft") focusInput(index - 1);
    if (event.key === "ArrowRight") focusInput(index + 1);
  };

  const onPaste = (event: ClipboardEvent) => {
    const pasted = event.clipboardData.getData("text").replace(/\D/g, "").slice(0, CODE_LENGTH);
    if (!pasted) return;
    event.preventDefault();
    fill(0, pasted);
  };

  const resend = async () => {
    if (cooldown > 0 || resending) return;
    setResending(true);
    setError("");
    setNotice("");
    try {
      await api.requestCode(email);
      setNotice(t.auth.codeResent);
      setCooldown(RESEND_COOLDOWN_SECONDS);
      reset();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setResending(false);
    }
  };

  return (
    <>
      <div className="auth-card-title">
        <ShieldCheck size={18} aria-hidden />
        <h2>{t.auth.verifyTitle}</h2>
      </div>
      <p className="auth-subtitle">
        {format(t.auth.verifySubtitle, { email: "" }).replace(/\s*\.\s*$/, "")}{" "}
        <strong>{maskEmail(email)}</strong>.
      </p>

      <div className="code-inputs" onPaste={onPaste}>
        {digits.map((digit, index) => (
          <input
            // The boxes are positional, so the index is the identity.
            key={index}
            ref={(element) => {
              inputs.current[index] = element;
            }}
            className={digit ? "code-input is-filled" : "code-input"}
            type="text"
            inputMode="numeric"
            autoComplete={index === 0 ? "one-time-code" : "off"}
            maxLength={CODE_LENGTH}
            value={digit}
            disabled={busy}
            aria-label={format(t.auth.digitLabel, { n: index + 1, total: CODE_LENGTH })}
            onChange={(event) => onChange(index, event.target.value)}
            onKeyDown={(event) => onKeyDown(index, event)}
            onFocus={(event) => event.target.select()}
          />
        ))}
      </div>

      {busy && (
        <p className="auth-status" role="status">
          <Loader2 size={15} className="spin" aria-hidden />
          {t.auth.validating}
        </p>
      )}
      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}
      {notice && !error && (
        <p className="auth-notice" role="status">
          {notice}
        </p>
      )}

      <div className="auth-actions">
        <button type="button" className="auth-link" disabled={cooldown > 0 || resending} onClick={() => void resend()}>
          <RefreshCw size={15} className={resending ? "spin" : undefined} aria-hidden />
          {cooldown > 0
            ? format(t.auth.resendIn, { sec: cooldown })
            : resending
              ? t.auth.resending
              : t.auth.resend}
        </button>
        <button type="button" className="auth-link" onClick={onBack}>
          <ArrowLeft size={15} aria-hidden />
          {t.auth.useAnotherEmail}
        </button>
      </div>
    </>
  );
}
