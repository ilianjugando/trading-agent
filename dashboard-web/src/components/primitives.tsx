import type { ReactNode, TdHTMLAttributes } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-border bg-card p-4 ${className}`}>
      {children}
    </div>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-muted">
      {children}
    </div>
  );
}

export function StatTile({
  label,
  value,
  sub,
  valueClassName = "",
}: {
  label: string;
  value: string;
  sub?: string;
  valueClassName?: string;
}) {
  return (
    <Card className="flex flex-col gap-1.5">
      <span className="text-[11px] uppercase tracking-wider text-muted">{label}</span>
      <span className={`font-mono text-2xl font-semibold tabular-nums ${valueClassName}`}>{value}</span>
      {sub && <span className="text-xs text-muted">{sub}</span>}
    </Card>
  );
}

const CHIP_STYLES: Record<string, string> = {
  ok: "bg-accent-soft text-accent",
  bad: "bg-bad-soft text-bad",
  warn: "bg-warn-soft text-warn",
  ai: "bg-ai-soft text-ai",
  muted: "bg-card-2 text-muted",
};

export function Chip({ tone = "muted", children }: { tone?: keyof typeof CHIP_STYLES; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wide ${CHIP_STYLES[tone]}`}>
      {children}
    </span>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-24 items-center justify-center rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted">
      {children}
    </div>
  );
}

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-max border-collapse text-sm">{children}</table>
    </div>
  );
}

export function Th({ children, align = "left" }: { children: ReactNode; align?: "left" | "right" }) {
  return (
    <th
      className={`border-b border-border py-2 pr-4 font-mono text-[11px] font-medium uppercase tracking-wide text-muted ${
        align === "right" ? "text-right" : "text-left"
      }`}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  align = "left",
  className = "",
  onClick,
  ...rest
}: {
  children: ReactNode;
  align?: "left" | "right";
  className?: string;
} & Omit<TdHTMLAttributes<HTMLTableCellElement>, "align" | "className">) {
  return (
    <td
      onClick={onClick}
      className={`border-b border-border/60 py-2 pr-4 ${align === "right" ? "text-right" : "text-left"} ${className}`}
      {...rest}
    >
      {children}
    </td>
  );
}
