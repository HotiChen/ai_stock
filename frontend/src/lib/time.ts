/**
 * Taiwan trading day time utilities.
 *
 * Implementations differ between call sites:
 *
 * - Portfolio.tsx (makeSecondsToClose) used 13:25 as the force-close target and
 *   clamped the result to ≥ 0 with Math.max.
 *
 * - AppChrome.tsx (getSecondsToClose) used 13:30 as the market-close target and
 *   returned the raw signed value (can be negative after close, so the topbar
 *   countdown display uses a `secondsToClose > -3600` guard rather than clamping).
 *
 * This module exposes two functions to preserve both behaviours:
 *   - getSecondsToForceClose() → 13:25 target, clamped to ≥ 0  (Portfolio)
 *   - getSecondsToMarketClose() → 13:30 target, signed           (AppChrome)
 */

/**
 * Seconds until 13:25 (Taiwan market force-close time).
 * Returns 0 once the deadline has passed.
 */
export function getSecondsToForceClose(now: Date = new Date()): number {
  const close = new Date(now);
  close.setHours(13, 25, 0, 0);
  return Math.max(0, Math.floor((close.getTime() - now.getTime()) / 1000));
}

/**
 * Seconds until 13:30 (Taiwan market official close).
 * Returns a signed value — negative after the close bell.
 * Used by AppChrome topbar to show "距收盤" countdown.
 */
export function getSecondsToMarketClose(now: Date = new Date()): number {
  const close = new Date(now);
  close.setHours(13, 30, 0, 0);
  return Math.floor((close.getTime() - now.getTime()) / 1000);
}
