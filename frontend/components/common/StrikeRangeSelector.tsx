"use client";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const STRIKE_OPTIONS = [
  { label: "±5", value: 5 },
  { label: "±10", value: 10 },
  { label: "±15", value: 15 },
  { label: "±20", value: 20 },
  { label: "All", value: 0 },
] as const;

interface StrikeRangeSelectorProps {
  value: number;
  onChange: (v: number) => void;
  className?: string;
}

export function StrikeRangeSelector({ value, onChange, className }: StrikeRangeSelectorProps) {
  return (
    <div
      className={cn("flex items-center gap-0.5 rounded-md border border-terminal-border p-0.5", className)}
      role="group"
      aria-label="Strike range around ATM"
    >
      {STRIKE_OPTIONS.map(({ label, value: v }) => (
        <Button
          key={v}
          size="sm"
          variant={value === v ? "default" : "ghost"}
          onClick={() => onChange(v)}
          aria-pressed={value === v}
          aria-label={`${label} strikes`}
          className="h-6 min-w-[36px] px-2 text-xs"
        >
          {label}
        </Button>
      ))}
    </div>
  );
}
