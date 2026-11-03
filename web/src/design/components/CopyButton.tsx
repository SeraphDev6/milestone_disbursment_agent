import { useState } from "react";
import { announce } from "./Announcer";
import { Button } from "./Button";

/** Copies a value and says so, visually and to screen readers. */
export function CopyButton({ value, label, children }: { value: string; label: string; children?: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      announce("Copied to clipboard");
      setTimeout(() => setCopied(false), 1600);
    } catch {
      announce("Couldn't copy. Select the text and copy it manually.", "assertive");
    }
  }
  return (
    <Button variant="quiet" size="sm" icon={copied ? "check" : "copy"} aria-label={children ? undefined : label} onPress={copy}>
      {children ? (copied ? "Copied" : children) : undefined}
    </Button>
  );
}
