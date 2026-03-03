/** Web Push notification helpers for AgentHive. */

import { authedFetch } from "./api";

const STORAGE_KEY = "agenthive-push-enabled";

export function isPushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window;
}

export function isPushEnabled() {
  return localStorage.getItem(STORAGE_KEY) === "true";
}

function setPushEnabled(v) {
  localStorage.setItem(STORAGE_KEY, v ? "true" : "false");
}

/**
 * Register service worker, subscribe to push, and send subscription to backend.
 * Returns true on success, false on failure.
 */
export async function setupPushNotifications() {
  if (!isPushSupported()) return false;
  // Skip service worker registration in dev mode — it conflicts with Vite HMR
  // and causes white screen crashes in standalone PWA mode.
  if (import.meta.env.DEV) return false;

  try {
    // Request notification permission
    const permission = await Notification.requestPermission();
    if (permission !== "granted") return false;

    // Use the SW already registered by vite-plugin-pwa (includes push-handler.js)
    const reg = await navigator.serviceWorker.ready;

    // Fetch VAPID public key from backend
    const res = await authedFetch("/api/push/vapid-public-key");
    if (!res.ok) return false;
    const { publicKey } = await res.json();

    // Convert base64url to Uint8Array
    const applicationServerKey = urlBase64ToUint8Array(publicKey);

    // Check for existing subscription first — re-subscribe if expired
    let subscription = await reg.pushManager.getSubscription();
    if (!subscription) {
      subscription = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey,
      });
    }

    // Send subscription to backend (always, to handle endpoint rotation)
    const sub = subscription.toJSON();
    const postRes = await authedFetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        endpoint: sub.endpoint,
        keys: sub.keys,
      }),
    });

    if (postRes.ok) {
      setPushEnabled(true);
      return true;
    }
    return false;
  } catch (err) {
    console.warn("Push setup failed:", err);
    return false;
  }
}

/**
 * Unsubscribe from push and notify backend.
 */
export async function teardownPushNotifications() {
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    if (reg) {
      const subscription = await reg.pushManager.getSubscription();
      if (subscription) {
        const endpoint = subscription.endpoint;
        await subscription.unsubscribe();

        await authedFetch("/api/push/unsubscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint }),
        });
      }
    }
  } catch (err) {
    console.warn("Push teardown error:", err);
  }
  setPushEnabled(false);
}

/** Convert a base64url-encoded string to a Uint8Array (for applicationServerKey). */
function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) {
    arr[i] = raw.charCodeAt(i);
  }
  return arr;
}
