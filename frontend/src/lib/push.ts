// T230-ALERTING-PUSH-NOTIFICATIONS: browser-side Web Push subscribe/unsubscribe helpers.
// Wraps the Service Worker + Push API + this app's own /push/* backend endpoints so
// settings.tsx (or any future caller) doesn't need to know the VAPID key encoding details.
import { api } from './api';

export function isPushSupported(): boolean {
  return typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window;
}

// The browser Push API requires the VAPID public key as a Uint8Array, but the key is
// generated/stored as a base64url string — this is the standard conversion every Web Push
// tutorial repeats, kept local rather than pulling in a dependency for one function.
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) outputArray[i] = rawData.charCodeAt(i);
  return outputArray;
}

export async function getExistingSubscription(): Promise<PushSubscription | null> {
  if (!isPushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration('/sw.js');
  if (!registration) return null;
  return registration.pushManager.getSubscription();
}

export async function enablePushNotifications(): Promise<{ ok: boolean; error?: string }> {
  if (!isPushSupported()) {
    return { ok: false, error: 'Push notifications are not supported in this browser.' };
  }

  const { public_key } = await api.pushVapidPublicKey();
  if (!public_key) {
    return { ok: false, error: 'Push notifications are not configured on this server yet.' };
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    return { ok: false, error: 'Notification permission was not granted.' };
  }

  const registration = await navigator.serviceWorker.register('/sw.js');
  await navigator.serviceWorker.ready;

  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(public_key),
    });
  }

  const json = subscription.toJSON();
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
    return { ok: false, error: 'Browser returned an incomplete push subscription.' };
  }

  await api.pushSubscribe({
    endpoint: json.endpoint,
    keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
    user_agent: navigator.userAgent,
  });

  return { ok: true };
}

export async function disablePushNotifications(): Promise<{ ok: boolean; error?: string }> {
  const subscription = await getExistingSubscription();
  if (!subscription) return { ok: true }; // already disabled — nothing to do
  const endpoint = subscription.endpoint;
  await subscription.unsubscribe();
  await api.pushUnsubscribe(endpoint);
  return { ok: true };
}
