/* Pointer and keyboard positioning for a floating button; dragging never activates it. */
(() => {
  function attach(button, { onActivate, storageKey = 'thermoflow-agent-position-v1' } = {}) {
    let saved = null, drag = null, suppressClick = false;
    try {
      const value = JSON.parse(window.localStorage.getItem(storageKey));
      if (value && Number.isFinite(value.x) && Number.isFinite(value.y)) saved = value;
    } catch { /* Positioning still works when local storage is unavailable. */ }
    function limits() {
      const rect = button.getBoundingClientRect();
      return { x: Math.max(8, window.innerWidth - (rect.width || 48) - 8),
        y: Math.max(8, window.innerHeight - (rect.height || 68) - 8) };
    }
    function place(x, y) {
      const max = limits();
      x = Math.min(max.x, Math.max(8, x));
      y = Math.min(max.y, Math.max(8, y));
      button.style.left = `${x}px`;
      button.style.top = `${y}px`;
      button.style.right = 'auto';
      return { x, y };
    }
    function remember() {
      const rect = button.getBoundingClientRect(), max = limits();
      saved = { x: (rect.x - 8) / Math.max(1, max.x - 8), y: (rect.y - 8) / Math.max(1, max.y - 8) };
      try { window.localStorage.setItem(storageKey, JSON.stringify(saved)); } catch { /* Optional persistence. */ }
    }
    function restore() {
      if (button.hidden || drag) return;
      const max = limits();
      place(saved ? 8 + saved.x * (max.x - 8) : max.x,
        saved ? 8 + saved.y * (max.y - 8) : Math.min(128, max.y));
    }
    function finish(cancelled = false) {
      if (!drag) return;
      const previous = drag;
      drag = null;
      suppressClick = previous.moved || cancelled;
      if (cancelled) place(previous.left, previous.top);
      else if (previous.moved) remember();
      button.classList.remove('is-dragging');
      if (button.hasPointerCapture(previous.id)) button.releasePointerCapture(previous.id);
    }
    button.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.isPrimary === false) return;
      suppressClick = false;
      const rect = button.getBoundingClientRect();
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.x, top: rect.y, moved: false };
      button.setPointerCapture(event.pointerId);
    });
    button.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.id) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (Math.hypot(dx, dy) > 5) drag.moved = true;
      if (!drag.moved) return;
      event.preventDefault();
      button.classList.add('is-dragging');
      place(drag.left + dx, drag.top + dy);
    });
    button.addEventListener('pointerup', event => { if (event.pointerId === drag?.id) finish(); });
    button.addEventListener('pointercancel', event => { if (event.pointerId === drag?.id) finish(true); });
    button.addEventListener('lostpointercapture', () => { if (drag) finish(true); });
    button.addEventListener('click', event => {
      if (suppressClick && event.detail !== 0) { event.preventDefault(); event.stopImmediatePropagation(); suppressClick = false; return; }
      onActivate?.();
    });
    button.addEventListener('keydown', event => {
      if (event.key === 'Escape' && drag) { event.preventDefault(); finish(true); return; }
      const direction = { ArrowLeft: [-1,0], ArrowRight: [1,0], ArrowUp: [0,-1], ArrowDown: [0,1] }[event.key];
      if (direction) {
        event.preventDefault();
        const rect = button.getBoundingClientRect(), step = event.shiftKey ? 1 : 10;
        place(rect.x + direction[0] * step, rect.y + direction[1] * step); remember();
      } else if (event.key === 'Home') {
        event.preventDefault(); saved = null; restore(); remember();
      }
    });
    window.addEventListener('resize', restore);
    restore();
    return { restore };
  }
  window.ThermoFlowFloatingButton = { attach };
})();
