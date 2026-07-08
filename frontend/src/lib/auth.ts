const JWT_KEY       = 'stockai_jwt';
const JWT_ADMIN_KEY = 'stockai_jwt_admin'; // saved admin token during impersonation

export interface Session {
  username: string;
  role: 'admin' | 'user';
}

function decodeJWT(token: string): Session | null {
  try {
    // JWT uses base64url (- and _ instead of + and /); atob requires standard base64
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const payload = JSON.parse(atob(b64));
    if (payload.exp < Date.now() / 1000) return null;
    return { username: payload.sub as string, role: payload.role as 'admin' | 'user' };
  } catch {
    return null;
  }
}

export async function login(username: string, password: string): Promise<boolean> {
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    const token: string | undefined = data.token;
    if (!token || typeof token !== 'string') return false;
    localStorage.setItem(JWT_KEY, token);
    // Verify the token is actually decodable before reporting success
    if (!decodeJWT(token)) return false;
    return true;
  } catch {
    return false;
  }
}

export function logout(): void {
  const token = localStorage.getItem(JWT_KEY);
  if (token) {
    // Fire-and-forget: revoke the token server-side. Don't await — local logout is instant.
    fetch('/api/auth/logout', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    }).catch(() => {});
  }
  localStorage.removeItem(JWT_KEY);
  localStorage.removeItem(JWT_ADMIN_KEY);
}

/** Swap in a user token while keeping the admin token for later restoration. */
export function startImpersonation(userToken: string): void {
  const adminToken = localStorage.getItem(JWT_KEY);
  if (adminToken) localStorage.setItem(JWT_ADMIN_KEY, adminToken);
  localStorage.setItem(JWT_KEY, userToken);
}

/** Restore the saved admin token and end the impersonation session. */
export function exitImpersonation(): void {
  const adminToken = localStorage.getItem(JWT_ADMIN_KEY);
  if (adminToken) localStorage.setItem(JWT_KEY, adminToken);
  localStorage.removeItem(JWT_ADMIN_KEY);
}

/** Returns the username being impersonated, or null if not impersonating. */
export function getImpersonatedUser(): string | null {
  if (typeof window === 'undefined') return null;
  const adminToken = localStorage.getItem(JWT_ADMIN_KEY);
  if (!adminToken) return null;
  const session = getSession();
  return session?.username ?? null;
}

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(JWT_KEY);
}

export function getSession(): Session | null {
  if (typeof window === 'undefined') return null;
  const token = localStorage.getItem(JWT_KEY);
  if (!token) return null;
  return decodeJWT(token);
}

export function isLoggedIn(): boolean {
  return getSession() !== null;
}

/** Returns the current username, used to namespace SWR cache keys per user. */
export function getUsername(): string {
  return getSession()?.username ?? '';
}

export async function resetPassword(
  username: string,
  oldPassword: string,
  newPassword: string,
): Promise<'ok' | 'wrong_password' | 'not_found' | 'error'> {
  try {
    const res = await fetch('/api/auth/reset-password', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ username, old_password: oldPassword, new_password: newPassword }),
    });
    if (res.status === 401) return 'wrong_password';
    if (res.status === 404) return 'not_found';
    if (!res.ok) return 'error';
    return 'ok';
  } catch {
    return 'error';
  }
}

export async function changePassword(
  oldPassword: string,
  newPassword: string,
): Promise<'ok' | 'wrong_password' | 'error'> {
  const token = getToken();
  if (!token) return 'error';
  try {
    const res = await fetch('/api/auth/change-password', {
      method: 'PUT',
      headers: { 'content-type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    });
    if (res.status === 401) return 'wrong_password';
    if (!res.ok) return 'error';
    return 'ok';
  } catch {
    return 'error';
  }
}
