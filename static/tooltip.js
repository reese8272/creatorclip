/**
 * Reusable tooltip component (Issue 124).
 *
 * Usage: add data-tooltip="..." to any element. Optionally add tabindex="0"
 * to make it keyboard-accessible. The tooltip text is read from the attribute.
 *
 * CSS-first (::after pseudo-element) with a minimal JS layer for:
 *   - viewport overflow correction (left/right flip)
 *   - Escape-key dismissal (WCAG 1.4.13)
 */
(function () {
  const style = document.createElement('style');
  style.textContent = `
    .info-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 14px;
      height: 14px;
      border-radius: 50%;
      border: 1px solid var(--color-border-strong, #888);
      color: var(--color-text-muted, #666);
      font-size: 10px;
      font-style: normal;
      font-weight: 600;
      font-family: var(--font-sans, sans-serif);
      cursor: default;
      vertical-align: middle;
      margin-left: 4px;
      flex-shrink: 0;
    }

    [data-tooltip] {
      position: relative;
    }

    [data-tooltip]::after {
      content: attr(data-tooltip);
      position: absolute;
      bottom: calc(100% + 8px);
      left: 50%;
      transform: translateX(-50%);
      background: var(--color-text, #1a1a1a);
      color: var(--color-bg, #fff);
      padding: 6px 10px;
      border-radius: 4px;
      font-size: 11px;
      font-family: var(--font-sans, system-ui, sans-serif);
      font-style: normal;
      font-weight: 400;
      text-transform: none;
      letter-spacing: 0;
      line-height: 1.45;
      white-space: normal;
      width: max-content;
      max-width: 240px;
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.12s ease;
      z-index: 200;
    }

    [data-tooltip]:hover::after,
    [data-tooltip]:focus::after,
    [data-tooltip].tooltip-open::after {
      opacity: 1;
    }

    /* Flip left when tooltip would overflow the right edge */
    [data-tooltip].tooltip-flip-left::after {
      left: auto;
      right: 0;
      transform: none;
    }

    /* Flip right when tooltip would overflow the left edge */
    [data-tooltip].tooltip-flip-right::after {
      left: 0;
      transform: none;
    }
  `;
  document.head.appendChild(style);

  function adjustPosition(el) {
    el.classList.remove('tooltip-flip-left', 'tooltip-flip-right');
    const rect = el.getBoundingClientRect();
    const tipWidth = 240;
    if (rect.left + rect.width / 2 + tipWidth / 2 > window.innerWidth - 8) {
      el.classList.add('tooltip-flip-left');
    } else if (rect.left + rect.width / 2 - tipWidth / 2 < 8) {
      el.classList.add('tooltip-flip-right');
    }
  }

  document.addEventListener('mouseenter', function (e) {
    const el = e.target.closest('[data-tooltip]');
    if (el) adjustPosition(el);
  }, true);

  // Dismiss on Escape (WCAG 1.4.13)
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      document.querySelectorAll('[data-tooltip].tooltip-open').forEach(function (el) {
        el.classList.remove('tooltip-open');
      });
      if (document.activeElement && document.activeElement.hasAttribute('data-tooltip')) {
        document.activeElement.blur();
      }
    }
  });
})();
