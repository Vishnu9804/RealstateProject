import { useCallback, useEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { phoneVerificationApi } from "../api/phoneVerificationApi";
import { ApiError } from "../api/client";
import { IconAlert, IconCheck, IconClose, IconEdit, IconRefresh, IconWhatsApp } from "./Icons";

const CODE_LENGTH = 4;
const RESEND_SECONDS = 30;

/**
 * The 4-digit code dialog.
 *
 * It exists because of one specific thing going wrong: on the public site
 * the only identity a visitor has is the number they type, and a typed
 * number can be anybody's. Someone entering a number that isn't theirs —
 * by mistake or otherwise — puts a stranger's phone on our enquiry, sends
 * that stranger our messages, and (worse) would have let them read back
 * whatever we already hold for it. A code sent to the number, typed back
 * here, is the smallest thing that rules all of that out.
 *
 * Speed is the whole design brief, because this stands between a visitor
 * and the form they actually came to fill in:
 *
 *  - The request fires from an effect on mount, so the code is already on
 *    its way while the boxes are still painting. The backend stores the
 *    code and returns immediately, sending the WhatsApp message on its own
 *    thread — nothing here waits on WhatsApp.
 *  - The message leads with the code, so it is readable straight from the
 *    notification without opening the chat.
 *  - Typing (or pasting, or an autofill) advances box to box on its own, so
 *    the only thing left to do once all four are filled is press Done.
 *
 * Checking only happens on that Done click (or Enter) — never mid-typing —
 * so a visitor who is still correcting a digit never sees a stray "wrong
 * code" flash from a code that was momentarily complete but wrong.
 *
 * The one thing it will not do is trap someone: if we have no linked
 * WhatsApp number to send from, `onUnavailable` fires and the site carries
 * on unverified rather than dead-ending a real enquiry over our own
 * plumbing being down.
 *
 * It also, quite often, never asks for a code at all. The first request can
 * come back "verified" — this browser already proved this exact number, or
 * the number is already one of our clients (see the backend's
 * otp_service.request_otp) — and then the boxes are never shown. That is
 * why the dialog opens on a small "checking" panel rather than straight on
 * four empty boxes: showing four boxes and snatching them away a moment
 * later reads as a glitch, while a beat of "checking your number" reads as
 * the answer arriving.
 *
 * Rendered through a portal onto <body>, and that is load-bearing rather
 * than tidiness. It used to render where it sat in the tree — inside the
 * contact section's <Reveal>, which carries `will-change: transform`. That
 * makes the Reveal a containing block for `position: fixed`, so the
 * backdrop was fixed to the SECTION, not the window: it covered the
 * enquiry card and centred the dialog on that card rather than on the
 * screen, which is why it kept appearing too high up the page. A portal
 * takes it out of every ancestor's containing block and stacking context
 * in one move.
 */
export default function PhoneVerifyDialog({
  phone,
  priorToken,
  onVerified,
  onUnavailable,
  onClose,
}: {
  /** As typed. The backend canonicalises it and returns the E.164 form. */
  phone: string;
  /** Any verification this browser is already holding. Sent with the first
   *  request so a number this browser has ALREADY confirmed is recognised
   *  and answered without another WhatsApp message. See
   *  api/phoneVerificationApi.ts's requestCode. */
  priorToken?: string | null;
  /** Called with or without a code having been typed — see the "verified"
   *  status above. */
  onVerified: (verifiedPhone: string, token: string, expiresInSeconds: number) => void;
  /** No WhatsApp connection available to send a code from — the caller
   *  lets the visitor continue without verifying. */
  onUnavailable: () => void;
  /** "Change number" / Escape / backdrop — the number is left unverified. */
  onClose: () => void;
}) {
  const [digits, setDigits] = useState<string[]>(() => Array(CODE_LENGTH).fill(""));
  // "checking" until the first request answers. It may never become "code"
  // at all: a number that needs no code resolves the whole dialog from
  // that first response. Resends never leave "code".
  const [phase, setPhase] = useState<"checking" | "code">("checking");
  const [sending, setSending] = useState(true);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(RESEND_SECONDS);

  const boxRefs = useRef<Array<HTMLInputElement | null>>([]);
  // Guards the mount-time send against React 18's double-invoked effects in
  // development — sending a real WhatsApp message twice is not a dev-only
  // annoyance, it is two notifications on a stranger's phone.
  const requested = useRef(false);
  // Both live in refs as well as state: the auto-submit fires from inside a
  // keystroke handler and must never act on a stale snapshot.
  const verifying = useRef(false);
  const verifiedAlready = useRef(false);

  const sendCode = useCallback(
    async (isResend: boolean) => {
      setSending(true);
      setError(null);
      try {
        const result = await phoneVerificationApi.requestCode(phone, isResend ? null : priorToken);
        if (result.status === "unavailable") {
          onUnavailable();
          return;
        }
        // No code needed — the number is confirmed as of now. Settled the
        // same way a correct code settles it, so every caller sees one
        // outcome and never has to know which route got here.
        if (result.status === "verified" && result.verification_token) {
          verifiedAlready.current = true;
          onVerified(result.phone ?? phone, result.verification_token, result.expires_in_seconds ?? 0);
          return;
        }
        setSecondsLeft(result.status === "cooldown" ? Math.max(1, result.retry_after_seconds) : RESEND_SECONDS);
        setNote(isResend ? "Sent again — check WhatsApp." : null);
        setPhase("code");
      } catch (failure) {
        // The boxes have to appear even on a failure: Resend lives beneath
        // them, and it is the only way out of a first request that broke.
        setPhase("code");
        setError(
          failure instanceof ApiError && failure.status === 400
            ? "That number doesn't look right. Please close this and check it."
            : "We couldn't send the code just now. Please check your connection and try again.",
        );
      } finally {
        setSending(false);
      }
    },
    // `priorToken` is read on the first send only and never changes while
    // this dialog is mounted, so it deliberately stays out of the deps —
    // including it would re-arm the mount effect for no behaviour change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [phone, onUnavailable, onVerified],
  );

  useEffect(() => {
    if (requested.current) return;
    requested.current = true;
    void sendCode(false);
  }, [sendCode]);

  // The resend countdown. Stops at zero rather than running forever.
  useEffect(() => {
    if (secondsLeft <= 0) return;
    const timer = window.setTimeout(() => setSecondsLeft((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [secondsLeft]);

  // Focus lands when the boxes appear, not on mount — during "checking"
  // there is nothing to focus, and grabbing focus early would scroll the
  // page behind the dialog on some mobile browsers.
  useEffect(() => {
    if (phase === "code") boxRefs.current[0]?.focus();
  }, [phase]);

  // Escape closes, and the page behind stops scrolling while this is open —
  // on a phone this dialog IS the screen.
  useEffect(() => {
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  const submitCode = useCallback(
    async (code: string) => {
      if (verifying.current || verifiedAlready.current) return;
      verifying.current = true;
      setChecking(true);
      setError(null);
      setNote(null);
      try {
        const result = await phoneVerificationApi.confirmCode(phone, code);
        verifiedAlready.current = true;
        onVerified(result.phone, result.verification_token, result.expires_in_seconds);
      } catch (failure) {
        setError(
          failure instanceof ApiError && failure.status === 400
            ? "That code isn't right, or it has expired. Please check WhatsApp and try again."
            : "We couldn't check that code just now. Please try once more.",
        );
        setDigits(Array(CODE_LENGTH).fill(""));
        boxRefs.current[0]?.focus();
      } finally {
        verifying.current = false;
        setChecking(false);
      }
    },
    [phone, onVerified],
  );

  function writeDigits(next: string[]) {
    setDigits(next);
    if (error) setError(null);
  }

  const code = digits.join("");
  const complete = digits.every((digit) => digit !== "");

  function onDone() {
    if (!complete || checking) return;
    void submitCode(code);
  }

  function onDigitChange(index: number, raw: string) {
    // Handles a typed character, an autofilled code, and a paste that lands
    // in one box, with the same three lines — take every digit given and
    // spread it forward from here.
    const incoming = raw.replace(/\D/g, "");
    if (!incoming) {
      const next = [...digits];
      next[index] = "";
      setDigits(next);
      return;
    }
    const next = [...digits];
    for (let offset = 0; offset < incoming.length && index + offset < CODE_LENGTH; offset += 1) {
      next[index + offset] = incoming[offset];
    }
    const landed = Math.min(index + incoming.length, CODE_LENGTH - 1);
    boxRefs.current[landed]?.focus();
    writeDigits(next);
  }

  function onDigitKeyDown(index: number, event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Backspace" && digits[index] === "" && index > 0) {
      // Backspace in an already-empty box steps back and clears the one
      // before it — the behaviour every code field on every app has.
      event.preventDefault();
      const next = [...digits];
      next[index - 1] = "";
      setDigits(next);
      boxRefs.current[index - 1]?.focus();
      return;
    }
    if (event.key === "ArrowLeft" && index > 0) boxRefs.current[index - 1]?.focus();
    if (event.key === "ArrowRight" && index < CODE_LENGTH - 1) boxRefs.current[index + 1]?.focus();
    if (event.key === "Enter") {
      // Never lets Enter reach the form this dialog was opened from —
      // implicit submission from inside a modal would send an enquiry the
      // visitor has not finished confirming.
      event.preventDefault();
      onDone();
    }
  }

  function onPaste(event: ClipboardEvent<HTMLInputElement>) {
    const pasted = event.clipboardData.getData("text").replace(/\D/g, "").slice(0, CODE_LENGTH);
    if (!pasted) return;
    event.preventDefault();
    const next = Array(CODE_LENGTH).fill("");
    for (let i = 0; i < pasted.length; i += 1) next[i] = pasted[i];
    boxRefs.current[Math.min(pasted.length, CODE_LENGTH - 1)]?.focus();
    writeDigits(next);
  }

  // Deliberately short-lived and deliberately quiet: for most people this
  // is one beat before the boxes, and for a returning client it is the
  // whole dialog. Nothing here moves except the spinner, so a fast answer
  // doesn't flash a half-built panel on the way past.
  if (phase === "checking") {
    return createPortal(
      <div className="otp-backdrop" role="presentation" onMouseDown={onClose}>
        <div className="otp otp--checking" role="dialog" aria-modal="true" aria-label="Confirming your number">
          <span className="otp__edge" aria-hidden="true" />
          <span className="otp__bloom" aria-hidden="true" />
          <span className="otp__icon">
            <IconWhatsApp size={22} />
          </span>
          <p className="otp__checking" aria-live="polite">
            <span className="spinner" />
            Checking your number…
          </p>
          <span className="otp__phone">{phone}</span>
        </div>
      </div>,
      document.body,
    );
  }

  return createPortal(
    <div className="otp-backdrop" role="presentation" onMouseDown={onClose}>
      <div
        className={`otp${error ? " is-shaking" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="otp-title"
        aria-describedby="otp-sub"
        onMouseDown={(event) => event.stopPropagation()}
      >
        {/* The gold hairline along the top edge, and the soft bloom behind
            the icon — the two things that make this read as the same
            surface as the property cards rather than a browser alert. */}
        <span className="otp__edge" aria-hidden="true" />
        <span className="otp__bloom" aria-hidden="true" />

        <button type="button" className="otp__close" onClick={onClose} aria-label="Close">
          <IconClose size={17} />
        </button>

        <span className="otp__icon">
          <IconWhatsApp size={22} />
        </span>

        <h3 className="otp__title" id="otp-title">
          Confirm your number
        </h3>
        <p className="otp__sub" id="otp-sub">
          {sending ? "Sending a 4-digit code to" : "We've sent a 4-digit code to"}
        </p>
        <span className="otp__phone">
          {phone}
          {!sending && <IconCheck size={13} />}
        </span>

        <div className="otp__boxes" onPaste={onPaste}>
          {digits.map((digit, index) => (
            <input
              key={index}
              ref={(element) => {
                boxRefs.current[index] = element;
              }}
              className={`otp__box${digit ? " is-filled" : ""}${error ? " is-error" : ""}`}
              type="text"
              inputMode="numeric"
              // Lets iOS/Android offer the code straight from the WhatsApp
              // notification instead of making them read and retype it.
              autoComplete={index === 0 ? "one-time-code" : "off"}
              maxLength={CODE_LENGTH}
              value={digit}
              disabled={checking}
              aria-label={`Digit ${index + 1}`}
              onChange={(event) => onDigitChange(index, event.target.value)}
              onKeyDown={(event) => onDigitKeyDown(index, event)}
              onFocus={(event) => event.target.select()}
            />
          ))}
        </div>

        {/* One reserved slot for both, so the dialog never jumps by a line
            height the moment a code is wrong or resent. */}
        <div className="otp__msg" aria-live="polite">
          {error ? (
            <p className="otp__error" role="alert">
              <IconAlert size={15} />
              {error}
            </p>
          ) : note ? (
            <p className="otp__note">
              <IconCheck size={14} />
              {note}
            </p>
          ) : (
            <p className="otp__hint">It shows right in your WhatsApp notification.</p>
          )}
        </div>

        <button type="button" className="btn btn--primary otp__done" onClick={onDone} disabled={!complete || checking}>
          {checking ? <span className="spinner" /> : null}
          {checking ? "Checking…" : "Confirm number"}
        </button>

        <div className="otp__actions">
          <button
            type="button"
            className="otp__action"
            onClick={() => void sendCode(true)}
            disabled={sending || secondsLeft > 0}
          >
            <IconRefresh size={15} />
            {secondsLeft > 0 ? `Resend in ${secondsLeft}s` : "Resend code"}
          </button>
          <button type="button" className="otp__action" onClick={onClose}>
            <IconEdit size={15} />
            Change number
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
