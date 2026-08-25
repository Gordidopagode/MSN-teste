const queryUrl = new URLSearchParams(window.location.search).get("server")?.trim() || "";
const configuredUrl = import.meta.env.VITE_MSN_SERVER_URL?.trim();
const candidateUrl = queryUrl || configuredUrl || "ws://localhost:8765";

/**
 * The Python MSN server is intentionally configured in one place. The launcher
 * can provide a session-specific endpoint through ?server=..., while
 * VITE_MSN_SERVER_URL remains the development/build-time fallback. Both ws://
 * and future wss:// endpoints are supported; the frontend does not create a
 * second backend.
 */
export const MSN_SERVER_URL = candidateUrl;
