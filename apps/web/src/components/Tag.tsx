import type { ReactNode } from "react";

export type TagTone = "good" | "warning" | "critical" | "stamp" | "muted";

/**
 * The site's one tag for a state worth noticing: a word in its tone, boxed by
 * a hairline of the same tone. A quiet tag drops the box, so common and healthy
 * states (a neutral signal, a run that succeeded) read as a word and the
 * notable ones are what the eye finds. Every status on the site uses it, so
 * two tags that mean the same thing always look the same.
 */
export function Tag({ tone, quiet = false, className, children }: {
  tone: TagTone;
  quiet?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return <span className={["tag", `tag--${tone}`, quiet && "tag--quiet", className].filter(Boolean).join(" ")}>{children}</span>;
}
