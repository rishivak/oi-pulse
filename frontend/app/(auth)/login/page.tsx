import Link from "next/link";
import { redirect } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const AUTH_ERROR_MESSAGES: Record<string, string> = {
  oauth_denied: "Upstox sign-in was cancelled or denied. Try again to continue.",
  invalid_request: "The sign-in request was incomplete. Start the login flow again.",
  invalid_state: "Your sign-in session expired before completion. Start the login flow again.",
  upstox_inactive_segments:
    "Your Upstox account has no active segments. Reactivate the required segment in Upstox app or web, then retry.",
  token_exchange_failed: "Upstox sign-in could not be completed. Please retry.",
};

type LoginPageProps = {
  searchParams?: {
    auth_error?: string;
  };
};

export default function LoginPage({ searchParams }: LoginPageProps) {
  const authError = searchParams?.auth_error;

  if (!authError) {
    redirect("/api/auth/login");
  }

  const message = AUTH_ERROR_MESSAGES[authError] ?? AUTH_ERROR_MESSAGES.token_exchange_failed;

  return (
    <Card className="w-full max-w-lg">
      <CardHeader className="flex-col items-start gap-2">
        <CardTitle>Upstox Sign-In Required</CardTitle>
        <p className="text-sm text-terminal-text">{message}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-terminal-muted">
          The dashboard stays available with stored data, but live market access needs a valid Upstox session.
        </p>
        <div className="flex flex-wrap gap-3">
          <Link
            href="/api/auth/login"
            className="inline-flex h-9 items-center justify-center rounded-md bg-accent px-4 text-sm font-medium text-white transition-colors hover:bg-accent/90"
          >
            Try Login Again
          </Link>
          <Link
            href="/"
            className="inline-flex h-9 items-center justify-center rounded-md border border-terminal-border px-4 text-sm font-medium text-terminal-text transition-colors hover:bg-terminal-surface"
          >
            Return Home
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
