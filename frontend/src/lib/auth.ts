const JWT_KEY = 'stockai_jwt';

export interface Session {
  username: string;
  role: 'admin' | 'user';
}

function decodeJWT(token: string): Session | null {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
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
    const { token } = await res.json();
    localStorage.setItem(JWT_KEY, token);
    return true;
  } catch {
    return false;
  }
}

export function logout(): void {
  localStorage.removeItem(JWT_KEY);
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
