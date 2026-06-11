import { useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';

interface NavEntry {
  kbd: string;
  path: string;
}

/**
 * Registers a window-level keydown listener that navigates to a route when the
 * corresponding shortcut key is pressed.
 *
 * Ignores key events when:
 *  - the event target is an input, textarea, select, or contentEditable element
 *  - any modifier key (ctrl / meta / alt) is held down
 *
 * @param navItems  Array of { kbd, path } entries derived from NAV_ITEMS.
 *                  The hook does not duplicate the route map — callers pass
 *                  the same NAV_ITEMS array used to render the sidebar.
 */
export function useKeyboardNav(navItems: NavEntry[]): void {
  const navigate = useNavigate();

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      // Ignore when modifier keys are held
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      // Ignore when focus is inside an editable element
      const target = e.target as HTMLElement;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement ||
        target.isContentEditable
      ) {
        return;
      }

      const key = e.key.toLowerCase();
      const match = navItems.find((item) => item.kbd.toLowerCase() === key);
      if (match) {
        navigate(match.path);
      }
    },
    [navigate, navItems],
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);
}
