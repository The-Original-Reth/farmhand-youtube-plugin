// The HTTP client every call goes through: where the API is, the request and its error, and request bodies.

// The plugin's own API, relative to its page: /plugins/stream/api inside Farmhand, /api opened on the plugin itself.
// (The page only ever talks to its own plugin, never to Farmhand or the cameras.)
export const API_BASE = "api";

export async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    let body = null;
    try {
      body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    const error = new Error(detail);
    error.status = res.status;
    error.body = body;
    throw error;
  }
  return res.status === 204 ? null : res.json();
}

// The words to show for a failed request (or anything else that went wrong)
export const errorText = (e) => e.message || String(e);

// A request whose body is JSON
export const jsonBody = (method, data) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

// A delete. The API refuses deletes without confirm=true; the UI's own confirmation
// dialog is the real guard, so every delete is made after the user said yes.
export const del = (path) => request(`${path}${path.includes("?") ? "&" : "?"}confirm=true`, { method: "DELETE" });

// A POST that sends nothing but the asking ("sync now", "connect")
export const post = (path) => request(path, { method: "POST" });

// this browser's time zone ("Europe/Amsterdam"), or "" if it can't say
export const timeZone = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
})();
