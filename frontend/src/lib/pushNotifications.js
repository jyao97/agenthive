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
  if (!isPushSupported()) {
    console.debug("[push] setup skipped: PushManager not supported");
    return false;
  }

  try {
    // Request notification permission
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      console.debug("[push] setup skipped: permission=%s", permission);
      return false;
    }

    // In dev mode, vite-plugin-pwa doesn't register a SW, so we manually
    // register the lightweight push-handler.js as a standalone service worker.
    if (import.meta.env.DEV) {
      const existing = await navigator.serviceWorker.getRegistration();
      if (!existing) {
        console.debug("[push] dev mode: registering push-handler.js as standalone SW");
        await navigator.serviceWorker.register("/push-handler.js");
      }
    }

    const reg = await navigator.serviceWorker.ready;

    // Fetch VAPID public key from backend
    const res = await authedFetch("/api/push/vapid-public-key");
    if (!res.ok) {
      console.warn("[push] VAPID key fetch failed: %d %s", res.status, res.statusText);
      return false;
    }
    const { publicKey } = await res.json();

    // Convert base64url to Uint8Array
    const applicationServerKey = urlBase64ToUint8Array(publicKey);

    // Always ensure we have a valid subscription and the backend knows about it.
    // Existing subscriptions may have been purged server-side (410 expired)
    // while the browser still holds a stale reference.
    let subscription = await reg.pushManager.getSubscription();
    const hadExisting = !!subscription;
    if (!subscription) {
      subscription = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey,
      });
    }

    // Always send to backend — handles re-registration after server-side
    // expiry cleanup and endpoint rotation.
    const sub = subscription.toJSON();
    console.debug(
      "[push] %s subscription → sending to backend (endpoint=%s…)",
      hadExisting ? "existing" : "new",
      sub.endpoint?.slice(0, 60),
    );
    const postRes = await authedFetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        endpoint: sub.endpoint,
        keys: sub.keys,
      }),
    });

    if (postRes.ok) {
      console.debug("[push] subscription registered with backend");
      setPushEnabled(true);
      return true;
    }
    console.warn("[push] backend rejected subscription:", postRes.status);
    return false;
  } catch (err) {
    console.warn("[push] setup failed:", err);
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

/**
 * Re-send existing push subscription to backend on every page load.
 * Works in dev mode (no SW registration needed) — just upserts whatever
 * the browser already has.  Ensures backend always has a fresh copy even
 * after server-side expiry cleanup or endpoint rotation.
 */
export async function reRegisterExistingSubscription() {
  if (!isPushSupported()) return;
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    if (!reg) {
      console.debug("[push] re-register: no service worker registered");
      return;
    }
    const subscription = await reg.pushManager.getSubscription();
    if (!subscription) {
      console.debug("[push] re-register: no existing subscription");
      return;
    }
    const sub = subscription.toJSON();
    if (!sub.endpoint || !sub.keys?.p256dh || !sub.keys?.auth) {
      console.debug("[push] re-register: subscription missing keys");
      return;
    }
    const res = await authedFetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        endpoint: sub.endpoint,
        keys: sub.keys,
      }),
    });
    if (res.ok) {
      console.debug("[push] re-register: upsert OK (endpoint=%s…)", sub.endpoint?.slice(0, 60));
      setPushEnabled(true);
    } else {
      console.warn("[push] re-register: backend rejected: %d", res.status);
    }
  } catch (err) {
    console.debug("[push] re-register failed: %s", err);
  }
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
