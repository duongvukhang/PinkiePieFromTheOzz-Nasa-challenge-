import { useId, useState, type InputHTMLAttributes } from "react";

interface FieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  hint?: string;
  error?: string;
  passwordToggle?: boolean;
}

export function Field({
  label,
  hint,
  error,
  passwordToggle = false,
  type = "text",
  className,
  onBlur,
  onInput,
  onInvalid,
  ...rest
}: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errId = `${id}-err`;
  const warnId = `${id}-warn`;
  const [revealed, setRevealed] = useState(false);
  const [showError, setShowError] = useState(false);
  const [customMessage, setCustomMessage] = useState("");

  const activeError = error ?? (showError ? customMessage : "");
  const describedBy =
    [hint ? hintId : "", activeError ? errId : ""].filter(Boolean).join(" ") || undefined;

  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
        {rest.required && (
          <span aria-hidden="true" style={{ color: "var(--accent)", marginInlineStart: 4 }}>
            *
          </span>
        )}
      </label>
      {hint && (
        <span id={hintId} className="field-hint">
          {hint}
        </span>
      )}
      <div className="input-wrap">
        <input
          {...rest}
          id={id}
          type={passwordToggle && revealed ? "text" : type}
          aria-invalid={activeError ? "true" : "false"}
          aria-describedby={describedBy}
          className={`input${passwordToggle ? " has-toggle" : ""}${activeError ? " user-invalid-fallback" : ""}${className ? ` ${className}` : ""}`}
          onBlur={(e) => {
            if (e.currentTarget.value.length > 0 || showError) {
              const valid = e.currentTarget.validity.valid;
              setShowError(!valid);
              setCustomMessage(valid ? "" : e.currentTarget.validationMessage);
            }
            onBlur?.(e);
          }}
          onInput={(e) => {
            if (showError && e.currentTarget.validity.valid) {
              setShowError(false);
              setCustomMessage("");
            }
            onInput?.(e);
          }}
          onInvalid={(e) => {
            e.preventDefault();
            setShowError(true);
            setCustomMessage(e.currentTarget.validationMessage);
            onInvalid?.(e);
          }}
        />
        {passwordToggle && (
          <>
            <button
              type="button"
              className="input-toggle"
              aria-pressed={revealed}
              aria-label={revealed ? "Hide password" : "Show password"}
              aria-describedby={warnId}
              onClick={() => setRevealed((v) => !v)}
            >
              {revealed ? "Hide" : "Show"}
            </button>
            <span id={warnId} className="visually-hidden">
              Warning: showing the password displays it on screen.
            </span>
          </>
        )}
      </div>
      {activeError && (
        <div id={errId} className="field-error">
          <span role="img" aria-label="Error">
            ⚠
          </span>{" "}
          {activeError}
        </div>
      )}
    </div>
  );
}
