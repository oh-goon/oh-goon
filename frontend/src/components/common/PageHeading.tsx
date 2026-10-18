import type { ReactNode } from "react";

interface PageHeadingProps {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}

export function PageHeading({ eyebrow, title, description, action }: PageHeadingProps) {
  return (
    <header className="page-heading">
      <div className="page-heading__copy">
        <span className="eyebrow">{eyebrow}</span>
        <h1 tabIndex={-1}>{title}</h1>
        <p>{description}</p>
      </div>
      {action ? <div className="page-heading__action">{action}</div> : null}
    </header>
  );
}
