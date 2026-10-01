import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ClipboardEvent, KeyboardEvent } from "react";

import { api, ApiError } from "../api/client";
import type { Access } from "../api/types";
import { useI18n } from "../i18n";

const CODE_LENGTH = 6;
const RESEND_COOLDOWN_SECONDS = 60;

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

  const complete = digits.every((digit) => digit !== "");

  return (
    <>
      <p className="auth-eyebrow">{t.auth.stepConfirm}</p>
      <h2 className="auth-heading">{t.auth.verifyTitle}</h2>
      <p className="auth-subtitle">{t.auth.verifySubtitle}</p>

      <p className="auth-field-label" id="code-label">
        {t.auth.codeLabel}
      </p>
      <div className="code-inputs" role="group" aria-labelledby="code-label" onPaste={onPaste}>
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
      <p className="auth-hint">
        {/* Split on the placeholder so the address can be set in bold. */}
        {t.auth.codeSentTo.split("{email}").map((part, index) => (
          <span key={index}>
            {index > 0 && <strong>{email}</strong>}
            {part}
          </span>
        ))}
      </p>

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

      <button
        type="button"
        className="auth-submit"
        disabled={!complete || busy}
        onClick={() => void submit(digits.join(""))}
      >
        {busy ? (
          <>
            <Loader2 size={16} className="spin" aria-hidden />
            {t.auth.validating}
          </>
        ) : (
          t.auth.confirm
        )}
      </button>

      <div className="auth-actions">
        <button type="button" className="auth-link is-accent" onClick={onBack}>
          {t.auth.useAnotherEmail}
        </button>
        <button
          type="button"
          className="auth-link"
          disabled={cooldown > 0 || resending}
          onClick={() => void resend()}
        >
          {cooldown > 0
            ? format(t.auth.resendIn, { sec: cooldown })
            : resending
              ? t.auth.resending
              : t.auth.resend}
        </button>
      </div>
    </>
  );
}
