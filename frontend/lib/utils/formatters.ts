import { format, parseISO } from "date-fns";

// ── Numbers ───────────────────────────────────────────────────────────────────

export function fmtNum(n: number | null | undefined, decimals = 0): string {
  if (n == null) return "—";
  return n.toLocaleString("en-IN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function fmtLakh(n: number | null | undefined): string {
  if (n == null) return "—";
  const val = n / 1_00_000;
  if (Math.abs(val) >= 100) return `${fmtNum(Math.round(val))}L`;
  return `${val.toFixed(2)}L`;
}

export function fmtCrore(n: number | null | undefined): string {
  if (n == null) return "—";
  const val = n / 1_00_00_000;
  return `${val.toFixed(2)}Cr`;
}

export function fmtPrice(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function fmtPct(n: number | null | undefined): string {
  if (n == null) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

export function fmtPCR(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toFixed(2);
}

export function fmtIV(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

// ── Dates ─────────────────────────────────────────────────────────────────────

export function fmtTime(iso: string): string {
  try {
    return format(parseISO(iso), "HH:mm:ss");
  } catch {
    return iso;
  }
}

export function fmtDate(iso: string): string {
  try {
    return format(parseISO(iso), "dd MMM yyyy");
  } catch {
    return iso;
  }
}

export function fmtDateTime(iso: string): string {
  try {
    return format(parseISO(iso), "dd MMM HH:mm");
  } catch {
    return iso;
  }
}

// ── OI delta direction ────────────────────────────────────────────────────────

export function deltaArrow(n: number | null | undefined): string {
  if (n == null || n === 0) return "→";
  return n > 0 ? "▲" : "▼";
}

export function deltaColorClass(n: number | null | undefined): string {
  if (n == null || n === 0) return "text-neutral-400";
  return n > 0 ? "text-bull" : "text-bear";
}

export function oiSignalLabel(signal: string): string {
  const labels: Record<string, string> = {
    long_buildup: "Long Build-up",
    short_buildup: "Short Build-up",
    long_unwinding: "Long Unwind",
    short_covering: "Short Cover",
    neutral: "Neutral",
  };
  return labels[signal] ?? signal;
}

export function oiSignalColorClass(signal: string): string {
  const map: Record<string, string> = {
    long_buildup: "text-bull",
    short_covering: "text-bull",
    short_buildup: "text-bear",
    long_unwinding: "text-bear",
    neutral: "text-neutral-400",
  };
  return map[signal] ?? "text-neutral-400";
}
