"use client";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const INTERVALS = [1, 3, 5, 10, 15, 30] as const;
export type Interval = (typeof INTERVALS)[number];

interface IntervalSelectorProps {
  value: Interval;
  onChange: (v: Interval) => void;
  className?: string;
}

export function IntervalSelector({ value, onChange, className }: IntervalSelectorProps) {
  return (
    <div
      className={cn("flex items-center gap-0.5 rounded-md border border-terminal-border p-0.5", className)}
      role="group"
      aria-label="Snapshot interval"
    >
      {INTERVALS.map((i) => (
        <Button
          key={i}
          size="sm"
          variant={value === i ? "default" : "ghost"}
          onClick={() => onChange(i)}
          aria-pressed={value === i}
          aria-label={`${i} minute interval`}
          className="h-6 min-w-[32px] px-2 text-xs"
        >
          {i}m
        </Button>
      ))}
    </div>
  );
}
