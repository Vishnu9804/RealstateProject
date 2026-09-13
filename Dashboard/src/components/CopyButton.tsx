import { useState } from "react";
import { IconCheck, IconCopy } from "./Icons";

/**
 * Copies `text` verbatim to the clipboard. Falls back to a hidden
 * textarea + execCommand for contexts where navigator.clipboard is
 * unavailable (e.g. an insecure/non-HTTPS origin on a LAN IP).
 */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path below */
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  async function handleClick() {
    const ok = await copyText(text);
    setState(ok ? "copied" : "failed");
    window.setTimeout(() => setState("idle"), 1800);
  }

  return (
    <button type="button" className={`copy-btn${state === "copied" ? " copy-btn--done" : ""}`} onClick={() => void handleClick()}>
      {state === "copied" ? <IconCheck size={14} /> : <IconCopy size={14} />}
      <span>{state === "copied" ? "Copied!" : state === "failed" ? "Press Ctrl+C" : label}</span>
    </button>
  );
}
