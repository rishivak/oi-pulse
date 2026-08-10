"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { authApi } from "@/lib/api/client";

const AUTH_ERROR_MESSAGES: Record<string, string> = {
  oauth_denied: "Upstox sign-in was cancelled or denied. Try again to continue.",
  invalid_request: "The sign-in request was incomplete. Start the login flow again.",
  invalid_state: "Your sign-in session expired before completion. Start the login flow again.",
  upstox_inactive_segments:
    "Your Upstox account has no active segments. Reactivate the required segment in Upstox app or web, then retry.",
  token_exchange_failed: "Upstox sign-in could not be completed. Please retry.",
};

function LoginContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const authError = searchParams.get("auth_error");

  const [offlinePending, setOfflinePending] = useState(false);
  const [offlineError, setOfflineError] = useState<string | null>(null);

  const message = authError
    ? AUTH_ERROR_MESSAGES[authError] ?? AUTH_ERROR_MESSAGES.token_exchange_failed
    : "Connect Upstox for live collection, or continue with snapshots already stored in this install.";

  async function continueWithStoredData() {
    setOfflinePending(true);
    setOfflineError(null);
    try {
      await authApi.offlineSession();
      router.replace("/");
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        "Could not open stored data. Connect Upstox once during market hours first.";
      setOfflineError(typeof detail === "string" ? detail : "Could not open stored data.");
    } finally {
      setOfflinePending(false);
    }
  }

  return (
    <Card className="w-full max-w-lg">
      <CardHeader className="flex-col items-start gap-2">
        <CardTitle>
          {authError ? "Upstox Sign-In Required" : "OI Pulse Access"}
        </CardTitle>
        <p className="text-sm text-terminal-text">{message}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-terminal-muted">
          The dashboard stays available with stored data, but live market access needs a valid Upstox session.
        </p>
        {offlineError && (
          <p className="text-sm text-bear" role="alert">
            {offlineError}
          </p>
        )}
        <div className="flex flex-wrap gap-3">
          <a
            href={authApi.loginUrl}
            className="inline-flex h-9 items-center justify-center rounded-md bg-accent px-4 text-sm font-medium text-white transition-colors hover:bg-accent/90"
          >
            {authError ? "Try Login Again" : "Connect Upstox"}
          </a>
          <Button
            variant="outline"
            onClick={continueWithStoredData}
            disabled={offlinePending}
          >
            {offlinePending ? "Opening…" : "Continue with stored data"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <Card className="w-full max-w-lg">
          <CardHeader>
            <CardTitle>OI Pulse Access</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-terminal-muted">Loading…</p>
          </CardContent>
        </Card>
      }
    >
      <LoginContent />
    </Suspense>
  );
}
