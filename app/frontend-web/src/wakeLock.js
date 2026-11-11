/** Screen Wake Lock while a session is recording. Re-request when the page is visible again. */
export function createWakeLock(nav = globalThis.navigator, doc = globalThis.document) {
  let sentinel = null;
  let wanted = false;

  async function acquire() {
    wanted = true;
    if (!nav || !nav.wakeLock || (doc && doc.visibilityState === 'hidden')) return false;
    try {
      sentinel = await nav.wakeLock.request('screen');
      sentinel.addEventListener('release', () => { sentinel = null; });
      return true;
    } catch {
      sentinel = null;
      return false;
    }
  }

  async function release() {
    wanted = false;
    const current = sentinel;
    sentinel = null;
    if (current) {
      try { await current.release(); } catch { /* already released */ }
    }
  }

  async function onVisible() {
    if (wanted && doc && doc.visibilityState === 'visible') return acquire();
    return false;
  }

  return {
    acquire,
    release,
    onVisible,
    get held() { return sentinel != null; },
    get wanted() { return wanted; },
  };
}
