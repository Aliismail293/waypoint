// Who is signed in, and the fetch helpers every view shares. The Google ID token lives only in memory.
export const session = { idToken: null, devMode: false };

export const canAsk = () => Boolean(session.idToken) || session.devMode;

export function authHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (session.idToken) headers.Authorization = `Bearer ${session.idToken}`;
  return headers;
}

export async function errorFrom(response) {
  const body = await response.json().catch(() => ({}));
  return new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
}

export async function api(method, path, body) {
  const response = await fetch(path, { method, headers: authHeaders(), body: body === undefined ? undefined : JSON.stringify(body) });
  if (!response.ok) throw await errorFrom(response);
  return response.json();
}

export const getJson = (path) => api("GET", path);
export const postJson = (path, body) => fetch(path, { method: "POST", headers: authHeaders(), body: JSON.stringify(body) });
