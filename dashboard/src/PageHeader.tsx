import type { ReactNode } from "react";

/** The top of every page: what it is, what it is for, and its main actions. */
export function PageHeader({
  title,
  purpose,
  children,
}: Readonly<{ title: string; purpose: string; children?: ReactNode }>) {
  return (
    <div className="page-header">
      <div>
        <h2>{title}</h2>
        <p className="muted">{purpose}</p>
      </div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  );
}
