import type { ReactNode } from "react";
import { Button as AriaButton, Link as AriaLink, type ButtonProps, type LinkProps } from "react-aria-components";
import { Glyph, type GlyphName } from "./Glyph";
import s from "./Button.module.css";

export type Variant = "primary" | "secondary" | "quiet" | "danger";
type Common = { variant?: Variant; size?: "md" | "sm"; icon?: GlyphName; iconEnd?: GlyphName; children?: ReactNode };

function classes(variant: Variant, size: "md" | "sm", iconOnly: boolean, extra?: string) {
  return [s.button, s[variant], size === "sm" && s.sm, iconOnly && s.icon, extra].filter(Boolean).join(" ");
}

/**
 * A button. `isPending` keeps it focusable and announces progress (React Aria), instead of disabling it.
 * Icon-only buttons must pass aria-label.
 */
export function Button({ variant = "secondary", size = "md", icon, iconEnd, children, className, ...rest }: Common & Omit<ButtonProps, "children" | "className"> & { className?: string }) {
  return (
    <AriaButton {...rest} className={classes(variant, size, !children, className)}>
      {({ isPending }) => (
        <>
          {isPending ? <span className={s.spinner} aria-hidden="true" /> : icon && <Glyph name={icon} />}
          {children}
          {iconEnd && <Glyph name={iconEnd} />}
          {isPending && <span className="sr-only">, working</span>}
        </>
      )}
    </AriaButton>
  );
}

/**
 * A link styled as a button (navigation, not an action). In-app paths go through the router; pass
 * `external` for anything outside the app (PayPal, API pages), which must not get the /app basename.
 */
export function ButtonLink({ variant = "secondary", size = "md", icon, iconEnd, children, className, external, ...rest }: Common & Omit<LinkProps, "children" | "className"> & { className?: string; external?: boolean }) {
  if (external) {
    const newTab = rest.target === "_blank";
    return (
      <a href={String(rest.href)} target={rest.target} rel={newTab ? "noopener noreferrer" : undefined} className={classes(variant, size, !children, className)}>
        {icon && <Glyph name={icon} />}
        {children}
        {iconEnd && <Glyph name={iconEnd} />}
        {newTab && <span className="sr-only"> (opens in a new tab)</span>}
      </a>
    );
  }
  return (
    <AriaLink {...rest} className={classes(variant, size, !children, className)}>
      {icon && <Glyph name={icon} />}
      {children}
      {iconEnd && <Glyph name={iconEnd} />}
      {rest.target === "_blank" && <span className="sr-only"> (opens in a new tab)</span>}
    </AriaLink>
  );
}
