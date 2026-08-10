"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { collectorApi, settingsApi } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { IntervalSelector, type Interval } from "@/components/common/IntervalSelector";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fmtDateTime } from "@/lib/utils/formatters";
import { Play, Square } from "lucide-react";

export default function SettingsPage() {
  const qc = useQueryClient();
  const [newUnderlying, setNewUnderlying] = useState("NIFTY");
  const [newInterval, setNewInterval] = useState<Interval>(5);

  const { data: jobs = [] } = useQuery({
    queryKey: queryKeys.collectorStatus,
    queryFn: collectorApi.status,
    refetchInterval: 10_000,
  });

  const { data: prefs } = useQuery({
    queryKey: queryKeys.settings,
    queryFn: settingsApi.get,
  });

  const startMut = useMutation({
    mutationFn: () => collectorApi.start(newUnderlying, newInterval),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.collectorStatus }),
  });

  const stopMut = useMutation({
    mutationFn: ({ underlying, interval_min }: { underlying: string; interval_min: number }) =>
      collectorApi.stop(underlying, interval_min),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.collectorStatus }),
  });

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <h1 className="text-lg font-semibold text-terminal-text">Settings</h1>

      {/* Collector control */}
      <Card>
        <CardHeader>
          <CardTitle>Collector Jobs</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Start new job */}
          <div className="flex flex-wrap items-center gap-3">
            <Select value={newUnderlying} onValueChange={setNewUnderlying}>
              <SelectTrigger className="w-32" aria-label="Underlying for new job">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {["NIFTY", "BANKNIFTY", "SENSEX"].map((u) => (
                  <SelectItem key={u} value={u}>{u}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <IntervalSelector value={newInterval} onChange={setNewInterval} />
            <Button
              size="sm"
              onClick={() => startMut.mutate()}
              disabled={startMut.isPending}
              className="gap-1.5"
            >
              <Play className="h-3.5 w-3.5" aria-hidden /> Start Collector
            </Button>
          </div>

          {/* Active jobs */}
          {jobs.length === 0 ? (
            <p className="text-sm text-terminal-muted">No collector jobs configured.</p>
          ) : (
            <div className="space-y-2">
              {jobs.map((job) => (
                <div
                  key={`${job.underlying}-${job.interval_min}`}
                  className="flex items-center justify-between rounded border border-terminal-border px-3 py-2"
                >
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-sm font-semibold">{job.underlying}</span>
                    <span className="text-xs text-terminal-muted">{job.interval_min}m interval</span>
                    <Badge variant={job.is_running ? "bull" : "neutral"}>
                      {job.is_running ? "Running" : "Stopped"}
                    </Badge>
                    {job.last_status === "failed" && (
                      <Badge variant="bear">Error</Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-3">
                    {job.last_run_at && (
                      <span className="text-xs text-terminal-muted">
                        {fmtDateTime(job.last_run_at)}
                      </span>
                    )}
                    {job.is_running && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1.5"
                        onClick={() =>
                          stopMut.mutate({
                            underlying: job.underlying,
                            interval_min: job.interval_min,
                          })
                        }
                        disabled={stopMut.isPending}
                      >
                        <Square className="h-3 w-3" aria-hidden /> Stop
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Connection info */}
      <Card>
        <CardHeader>
          <CardTitle>Upstox Connection</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3">
            <Button
              variant="outline"
              onClick={() => (window.location.href = "/api/auth/login")}
            >
              Re-connect Upstox Account
            </Button>
            <span className="text-xs text-terminal-muted">
              Opens Upstox OAuth login in same window
            </span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
