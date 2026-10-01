import { Loader2 } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { useLocation } from "wouter";

import { api, ApiError } from "../api/client";
import type { Access } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { AuthLayout } from "../components/AuthLayout";
import { useI18n } from "../i18n";
import { VerifyCode } from "./VerifyCode";

export function SignIn() {
  const [, navigate] = useLocation();
  const { signIn } = useAuth();
  const { t, format } = useI18n();

  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  // How long the code lasts, as the server said when it sent one. Unknown until
  // then, and the footnote simply leaves the number out.
  const [expiresMinutes, setExpiresMinutes] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submitEmail = async (event: FormEvent) => {
    event.preventDefault();
    const normalized = email.trim().toLowerCase();
    if (!normalized.includes("@") || busy) return;

    setBusy(true);
    setError("");
    try {
      const sent = await api.requestCode(normalized);
      setExpiresMinutes(sent.expires_minutes ?? null);
      setEmail(normalized);
      setStep("code");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const onVerified = (access: Access) => {
    signIn(access);
    navigate("/worklist", { replace: true });
  };

  const footnote =
    step === "code" && expiresMinutes
      ? `${format(t.auth.codeValidFor, { minutes: expiresMinutes })} ${t.auth.authorizedOnly}`
      : t.auth.authorizedOnly;

  return (
    <AuthLayout footnote={footnote}>
      {step === "email" ? (
        <>
          <p className="auth-eyebrow">{t.auth.stepIdentify}</p>
          <h2 className="auth-heading">{t.auth.title}</h2>
          <p className="auth-subtitle">{t.auth.subtitle}</p>

          <form onSubmit={(event) => void submitEmail(event)}>
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
                onChange={(event) => {
                  setEmail(event.target.value);
                  setError("");
                }}
              />
            </label>

            {error && (
              <p className="auth-error" role="alert">
                {error}
              </p>
            )}

            <button className="auth-submit" type="submit" disabled={busy || !email.trim()}>
              {busy ? (
                <>
                  <Loader2 size={16} className="spin" aria-hidden />
                  {t.auth.sending}
                </>
              ) : (
                t.auth.sendCode
              )}
            </button>
          </form>
        </>
      ) : (
        <VerifyCode
          email={email}
          onVerified={onVerified}
          onBack={() => {
            setStep("email");
            setError("");
          }}
        />
      )}
    </AuthLayout>
  );
}
