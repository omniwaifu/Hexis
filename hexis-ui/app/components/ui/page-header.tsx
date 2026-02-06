interface PageHeaderProps {
  title: string;
  subtitle?: string;
  breadcrumb?: string;
}

export function PageHeader({ title, subtitle, breadcrumb = "Hexis" }: PageHeaderProps) {
  return (
    <header className="flex flex-col gap-2">
      <p className="text-xs uppercase tracking-[0.3em] text-[var(--teal)]">
        {breadcrumb}
      </p>
      <h1 className="font-display text-3xl leading-tight text-[var(--foreground)] md:text-4xl">
        {title}
      </h1>
      {subtitle && (
        <p className="max-w-2xl text-sm text-[var(--ink-soft)]">{subtitle}</p>
      )}
    </header>
  );
}
