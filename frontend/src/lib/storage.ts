import { getSession } from './auth';

function ns(key: string): string {
  const session = getSession();
  const username = session?.username ?? '__guest__';
  return `stockai:${username}:${key}`;
}

export const storage = {
  getItem(key: string): string | null {
    if (typeof window === 'undefined') return null;
    return localStorage.getItem(ns(key));
  },
  setItem(key: string, value: string): void {
    if (typeof window === 'undefined') return;
    localStorage.setItem(ns(key), value);
  },
  removeItem(key: string): void {
    if (typeof window === 'undefined') return;
    localStorage.removeItem(ns(key));
  },
};
