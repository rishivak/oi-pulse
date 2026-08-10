"use client";

import { useEffect } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MainNav } from "@/components/layout/MainNav";
import { sseClient } from "@/lib/realtime/sse-client";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 2,
    },
  },
});

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    sseClient.connect();
    return () => sseClient.disconnect();
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="flex h-screen flex-col bg-terminal-bg text-terminal-text">
        <MainNav />
        <main className="flex-1 overflow-auto">{children}</main>
      </div>
    </QueryClientProvider>
  );
}
