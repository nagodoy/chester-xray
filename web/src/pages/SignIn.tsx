import { ChevronRight, ShieldCheck, XCircle } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { useLocation } from "wouter";

import { api } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useI18n } from "../i18n";

const CODE_LENGTH = 6;

export function SignIn() {
  const [, navigate] = useLocation();
  const { signIn } = useAuth();
  const { t, format } = useI18n();

  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const requestCode = async () => {
    const normalized = email.trim().toLowerCase();
    if (!normalized || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.requestCode(normalized);
      setEmail(normalized);
      setCode("");
      setStep("code");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const submitEmail = (event: FormEvent) => {
    event.preventDefault();
    void requestCode();
  };

  const submitCode = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || code.length !== CODE_LENGTH) return;
    setBusy(true);
    setError("");
    try {
      signIn(await api.verifyCode(email, code));
      navigate("/worklist", { replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-shell">
      <div className="auth-wrap">
        <div className="auth-mark">
          <span className="brand-mark" aria-hidden>
            ⌁
          </span>
          <p>{t.brand.tagline}</p>
        </div>

        <section className="auth-card" aria-labelledby="auth-title">
          {step === "email" ? (
            <>
              <h1 id="auth-title">{t.auth.title}</h1>
              <p className="auth-subtitle">{t.auth.subtitle}</p>
              <form onSubmit={submitEmail}>
                <label className="auth-field">
                  <span>{t.auth.emailLabel}</span>
                  <input
                    className="auth-input"
                    type="email"
                    autoComplete="email"
                    autoFocus
                    required
                    value={email}
                    placeholder={t.auth.emailPlaceholder}
                    onChange={(event) => setEmail(event.target.value)}
                  />
                </label>
                {error && (
                  <p className="auth-error" role="alert">
                    <XCircle size={14} aria-hidden />
                    {error}
                  </p>
                )}
                <button className="auth-submit" type="submit" disabled={busy}>
                  {busy ? t.auth.sending : t.auth.continue}
                  <ChevronRight size={15} aria-hidden />
                </button>
              </form>
            </>
          ) : (
            <>
              <h1 id="auth-title">{t.auth.verifyTitle}</h1>
              <p className="auth-subtitle">{format(t.auth.verifySubtitle, { email })}</p>
              <form onSubmit={submitCode}>
                <label className="auth-field">
                  <span>{t.auth.codeLabel}</span>
                  <input
                    className="auth-input auth-code"
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    autoFocus
                    required
                    placeholder="000000"
                    value={code}
                    onChange={(event) =>
                      setCode(event.target.value.replace(/\D/g, "").slice(0, CODE_LENGTH))
                    }
                  />
                </label>
                {error && (
                  <p className="auth-error" role="alert">
                    <XCircle size={14} aria-hidden />
                    {error}
                  </p>
                )}
                <button
                  className="auth-submit"
                  type="submit"
                  disabled={busy || code.length < CODE_LENGTH}
                >
                  {busy ? t.auth.validating : t.auth.confirm}
                  <ChevronRight size={15} aria-hidden />
                </button>
              </form>
              <button
                className="auth-back"
                type="button"
                disabled={busy}
                onClick={() => void requestCode()}
              >
                {t.auth.resend}
              </button>
              <button
                className="auth-back"
                type="button"
                disabled={busy}
                onClick={() => {
                  setStep("email");
                  setCode("");
                  setError("");
                }}
              >
                {t.auth.useAnotherEmail}
              </button>
            </>
          )}
        </section>

        <div className="auth-note">
          <ShieldCheck size={14} aria-hidden />
          <span>{t.auth.researchOnly}</span>
        </div>
        <p className="auth-restricted">{t.auth.restricted}</p>
      </div>
    </div>
  );
}
