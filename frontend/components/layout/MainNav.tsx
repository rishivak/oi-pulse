"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  TrendingUp,
  ListFilter,
  Flame,
  LineChart,
  History,
  Bell,
  Settings,
  Monitor,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/trending-oi", label: "Trending OI", icon: TrendingUp },
  { href: "/option-chain", label: "Option Chain", icon: ListFilter },
  { href: "/oi-heatmap", label: "OI Heatmap", icon: Flame },
  { href: "/oi-charts", label: "OI Charts", icon: LineChart },
  { href: "/oi-history", label: "OI History", icon: History },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/terminal", label: "v2 Terminal", icon: Monitor },
] as const;

export function MainNav() {
  const pathname = usePathname();

  return (
    <nav
      className="flex items-center gap-0.5 overflow-x-auto border-b border-terminal-border bg-terminal-surface px-4"
      aria-label="Main navigation"
    >
      {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
        const active = pathname === href;
        return (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-3 text-sm transition-colors",
              active
                ? "border-accent text-terminal-text"
                : "border-transparent text-terminal-muted hover:text-terminal-text",
            )}
            aria-current={active ? "page" : undefined}
          >
            <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
