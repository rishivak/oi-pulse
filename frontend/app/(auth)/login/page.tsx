import { redirect } from "next/navigation";

// Unauthenticated users are sent here by the API client; start OAuth immediately.
export default function RootPage() {
  redirect("/api/auth/login");
}
