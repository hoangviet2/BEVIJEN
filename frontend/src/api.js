// All backend access goes through here: the REST API only, never AWS directly.
import { sessionUser } from './lib.js';

export const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');
export const DEV_AUTH = import.meta.env.VITE_AUTH_MODE === 'dev';
export const BACKEND_URL = (import.meta.env.VITE_BACKEND_URL || 'https://52-41-36-135.sslip.io').replace(/\/$/, '');
// Absolute on purpose: /auth/login sets a state cookie that /auth/callback (on the server's origin) must see.
export const LOGIN_URL = BACKEND_URL + '/auth/login';
const LOGOUT_URL = BACKEND_URL + '/auth/logout';

const KEY = 're_session';
export function getSession() {
  try { return JSON.parse(localStorage.getItem(KEY)) || null; } catch { return null; }
}
export function setSession(s) {
  try { s ? localStorage.setItem(KEY, JSON.stringify(s)) : localStorage.removeItem(KEY); } catch { /* private mode */ }
  window.dispatchEvent(new Event('re-auth'));
}
export const currentUser = () => sessionUser(getSession());

/** Drop our token, then end the Cognito Hosted UI session too, or the next sign-in skips the password. */
export function signOut() {
  const hadToken = !!getSession()?.token;
  setSession(null);
  if (hadToken) location.href = LOGOUT_URL;
}

/** /auth/callback lands here with #id_token=…; keep it and clean the URL. */
export function captureTokenFromHash() {
  const m = location.hash.match(/id_token=([^&]+)/);
  if (!m) return;
  setSession({ token: decodeURIComponent(m[1]) });
  history.replaceState(null, '', location.pathname + location.search);
}

export async function api(path, { method = 'GET', body, headers = {} } = {}) {
  const s = getSession() || {};
  const h = { ...headers };
  if (body !== undefined) h['Content-Type'] = 'application/json';
  if (s.token) h.Authorization = 'Bearer ' + s.token;
  if (s.devUser) { h['X-Dev-User'] = s.devUser; h['X-Dev-Groups'] = (s.devGroups || []).join(','); }
  const res = await fetch(API_BASE + path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.status === 401) setSession(null); // token expired (1 h, no refresh): back to sign-in
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); if (j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch { /* not json */ }
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

export const post = (path, body = {}) => api(path, { method: 'POST', body });
export const enc = encodeURIComponent;
