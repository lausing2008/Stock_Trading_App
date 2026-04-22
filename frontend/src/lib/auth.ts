const USERS_KEY = 'stockai_auth_users';
const SESSION_KEY = 'stockai_auth_session';

const DEFAULT_USERS: Record<string, string> = { lausing: '120402' };

function getUsers(): Record<string, string> {
  if (typeof window === 'undefined') return DEFAULT_USERS;
  try {
    const stored = localStorage.getItem(USERS_KEY);
    if (!stored) return DEFAULT_USERS;
    const parsed = JSON.parse(stored);
    // merge defaults so built-in account always exists unless overridden
    return { ...DEFAULT_USERS, ...parsed };
  } catch { return DEFAULT_USERS; }
}

export function login(username: string, password: string): boolean {
  const users = getUsers();
  if (users[username.toLowerCase()] !== password) return false;
  localStorage.setItem(SESSION_KEY, JSON.stringify({ username: username.toLowerCase() }));
  return true;
}

export function logout() {
  localStorage.removeItem(SESSION_KEY);
}

export function getSession(): { username: string } | null {
  if (typeof window === 'undefined') return null;
  try {
    const s = localStorage.getItem(SESSION_KEY);
    return s ? JSON.parse(s) : null;
  } catch { return null; }
}

export function isLoggedIn(): boolean {
  return getSession() !== null;
}

export function resetPassword(username: string, oldPassword: string, newPassword: string): 'ok' | 'wrong_password' | 'not_found' {
  const users = getUsers();
  const key = username.toLowerCase();
  if (!(key in users)) return 'not_found';
  if (users[key] !== oldPassword) return 'wrong_password';
  const updated = { ...users, [key]: newPassword };
  localStorage.setItem(USERS_KEY, JSON.stringify(updated));
  return 'ok';
}
