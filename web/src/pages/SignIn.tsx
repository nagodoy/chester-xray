import { ChevronRight, Loader2, Mail } from "lucide-react";
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
  const { t } = useI18n();

  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submitEmail = async (event: FormEvent) => {
    event.preventDefault();
    const normalized = email.trim().toLowerCase();
    if (!normalized.includes("@") || busy) return;

    setBusy(true);
    setError("");
    try {
      await api.requestCode(normalized);
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

  return (
    <AuthLayout>
      {step === "email" ? (
        <>
          <div className="auth-card-title">
            <h2>{t.auth.title}</h2>
          </div>
          <p className="auth-subtitle">{t.auth.subtitle}</p>

          <form onSubmit={(event) => void submitEmail(event)}>
            <label className="auth-field">
              <span>{t.auth.emailLabel}</span>
              <div className="auth-input-wrap">
                <Mail size={16} aria-hidden />
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
              </div>
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
                <>
                  {t.auth.continue}
                  <ChevronRight size={16} aria-hidden />
                </>
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
