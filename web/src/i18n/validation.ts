import type { Study } from "../api/types";
import { interpolate } from ".";
import type { Dictionary } from "./locales/pt-BR";

type ValidationKey = keyof Dictionary["validation"];

/**
 * Render a study's validation reason in the active language.
 *
 * The server sends a stable code plus the fields any message needs. It also
 * sends English prose, which is the fallback when a code arrives that this
 * build has no translation for -- an older interface against a newer server
 * should show a sentence, not a bare identifier.
 */
export function validationReason(study: Study, dictionary: Dictionary): string {
  const code = study.validation_reason_code;
  if (code && code in dictionary.validation) {
    return interpolate(dictionary.validation[code as ValidationKey], {
      modality: study.modality ?? "",
      body_part: study.body_part ?? "",
    });
  }
  return study.validation_reason ?? "";
}
