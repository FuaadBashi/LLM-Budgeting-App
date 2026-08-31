import { LoginGate } from "@/components/LoginGate";
import { getSession } from "@/lib/api";

/**
 * Returns a login screen when the API requires a session and there is not one.
 *
 * Checked server-side on every render rather than trusted from a cookie the
 * client can see: the API is the authority on whether a session is valid, and
 * anything the browser could inspect is a claim rather than a credential.
 *
 * Returns null when the app is open or the session is good, so callers render
 * their page as normal.
 */
export async function requireSession(): Promise<React.ReactNode | null> {
  try {
    const session = await getSession();
    if (session.auth_enabled && !session.authenticated) return <LoginGate />;
    return null;
  } catch {
    // The API being unreachable is a different failure; let the page report it.
    return null;
  }
}
