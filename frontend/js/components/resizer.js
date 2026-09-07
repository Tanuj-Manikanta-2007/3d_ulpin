/**
 * resizer.js — LeetCode-Style Split-Pane Resizer Controller
 * Coordinates draggable gutters between 2D Map, 3D Digital Twin, and Inspector Dossier.
 */

class SplitPaneManager {
  constructor(options = {}) {
    this.container = document.querySelector(options.container || '.app-container');
    this.paneMap = document.getElementById(options.paneMap || 'pane-map2d');
    this.pane3D = document.getElementById(options.pane3D || 'pane-view3d');
    this.paneInspector = document.getElementById(options.paneInspector || 'pane-inspector');
    this.gutter1 = document.getElementById(options.gutter1 || 'gutter-1');
    this.gutter2 = document.getElementById(options.gutter2 || 'gutter-2');

    this.onResizeCallback = options.onResize || null;

    this.minMapWidth = options.minMapWidth || 200;
    this.min3DWidth = options.min3DWidth || 240;
    this.minInspectorWidth = options.minInspectorWidth || 260;
    this.maxInspectorWidth = options.maxInspectorWidth || 680;

    this.activeGutter = null;
    this.startX = 0;
    this.startMapWidth = 0;
    this.start3DWidth = 0;
    this.startInspectorWidth = 0;
    this.animationFrameId = null;

    this.init();
  }

  init() {
    if (!this.container || !this.paneMap || !this.pane3D || !this.paneInspector) {
      console.warn('[SplitPaneManager] Panes not found in DOM.');
      return;
    }

    // Apply saved or default dimensions
    this.restoreLayout();

    // Event listeners for Gutter 1 (Map <-> 3D)
    if (this.gutter1) {
      this.gutter1.addEventListener('mousedown', (e) => this.onMouseDown(e, 1));
      this.gutter1.addEventListener('touchstart', (e) => this.onTouchStart(e, 1), { passive: false });
      this.gutter1.addEventListener('dblclick', () => this.resetLayout());
    }

    // Event listeners for Gutter 2 (3D <-> Inspector)
    if (this.gutter2) {
      this.gutter2.addEventListener('mousedown', (e) => this.onMouseDown(e, 2));
      this.gutter2.addEventListener('touchstart', (e) => this.onTouchStart(e, 2), { passive: false });
      this.gutter2.addEventListener('dblclick', () => this.resetLayout());
    }

    // Global drag & release handlers
    window.addEventListener('mousemove', (e) => this.onMouseMove(e));
    window.addEventListener('touchmove', (e) => this.onTouchMove(e), { passive: false });
    window.addEventListener('mouseup', () => this.onMouseUp());
    window.addEventListener('touchend', () => this.onMouseUp());
    window.addEventListener('resize', () => this.handleWindowResize());
  }

  restoreLayout() {
    try {
      const saved = JSON.parse(localStorage.getItem('3d_ulpin_panes_layout'));
      if (saved && saved.mapWidth && saved.inspectorWidth) {
        this.paneMap.style.flex = 'none';
        this.paneMap.style.width = `${Math.max(this.minMapWidth, saved.mapWidth)}px`;

        this.paneInspector.style.flex = 'none';
        this.paneInspector.style.width = `${Math.min(this.maxInspectorWidth, Math.max(this.minInspectorWidth, saved.inspectorWidth))}px`;

        this.pane3D.style.flex = '1';
        this.pane3D.style.width = 'auto';
        this.triggerResize();
        return;
      }
    } catch (_) {}

    // Default proportional layout: 36% Map, 1fr 3D, 380px Inspector
    this.paneMap.style.flex = 'none';
    this.paneMap.style.width = '36%';
    this.paneInspector.style.flex = 'none';
    this.paneInspector.style.width = '380px';
    this.pane3D.style.flex = '1';
    this.pane3D.style.width = 'auto';
    this.triggerResize();
  }

  saveLayout() {
    try {
      const mapWidth = this.paneMap.getBoundingClientRect().width;
      const inspectorWidth = this.paneInspector.getBoundingClientRect().width;
      localStorage.setItem('3d_ulpin_panes_layout', JSON.stringify({ mapWidth, inspectorWidth }));
    } catch (_) {}
  }

  resetLayout() {
    localStorage.removeItem('3d_ulpin_panes_layout');
    this.paneMap.style.flex = 'none';
    this.paneMap.style.width = '36%';
    this.paneInspector.style.flex = 'none';
    this.paneInspector.style.width = '380px';
    this.pane3D.style.flex = '1';
    this.pane3D.style.width = 'auto';
    this.triggerResize();
  }

  onMouseDown(e, gutterNum) {
    if (e.button !== 0) return; // Only left-click
    e.preventDefault();
    this.startDrag(e.clientX, gutterNum);
  }

  onTouchStart(e, gutterNum) {
    if (e.touches.length === 1) {
      e.preventDefault();
      this.startDrag(e.touches[0].clientX, gutterNum);
    }
  }

  startDrag(clientX, gutterNum) {
    this.activeGutter = gutterNum;
    this.startX = clientX;

    const mapRect = this.paneMap.getBoundingClientRect();
    const threeRect = this.pane3D.getBoundingClientRect();
    const inspRect = this.paneInspector.getBoundingClientRect();

    this.startMapWidth = mapRect.width;
    this.start3DWidth = threeRect.width;
    this.startInspectorWidth = inspRect.width;

    document.body.classList.add('resizing-active');
    if (gutterNum === 1 && this.gutter1) this.gutter1.classList.add('is-dragging');
    if (gutterNum === 2 && this.gutter2) this.gutter2.classList.add('is-dragging');
  }

  onMouseMove(e) {
    if (!this.activeGutter) return;
    e.preventDefault();
    this.handleDrag(e.clientX);
  }

  onTouchMove(e) {
    if (!this.activeGutter || e.touches.length === 0) return;
    e.preventDefault();
    this.handleDrag(e.touches[0].clientX);
  }

  handleDrag(clientX) {
    const deltaX = clientX - this.startX;
    const containerWidth = this.container.getBoundingClientRect().width;
    const guttersWidth = 14; // 7px * 2

    if (this.activeGutter === 1) {
      // Gutter 1: adjusts Map width and 3D width, leaves Inspector fixed
      let newMapWidth = this.startMapWidth + deltaX;
      const maxMapWidth = containerWidth - this.min3DWidth - this.startInspectorWidth - guttersWidth;

      if (newMapWidth < this.minMapWidth) newMapWidth = this.minMapWidth;
      if (newMapWidth > maxMapWidth) newMapWidth = maxMapWidth;

      this.paneMap.style.flex = 'none';
      this.paneMap.style.width = `${newMapWidth}px`;
      this.pane3D.style.flex = '1';
      this.pane3D.style.width = 'auto';

    } else if (this.activeGutter === 2) {
      // Gutter 2: adjusts 3D width and Inspector width, leaves Map fixed
      let newInspWidth = this.startInspectorWidth - deltaX;
      const maxInspWidth = Math.min(
        this.maxInspectorWidth,
        containerWidth - this.startMapWidth - this.min3DWidth - guttersWidth
      );

      if (newInspWidth < this.minInspectorWidth) newInspWidth = this.minInspectorWidth;
      if (newInspWidth > maxInspWidth) newInspWidth = maxInspWidth;

      this.paneInspector.style.flex = 'none';
      this.paneInspector.style.width = `${newInspWidth}px`;
      this.pane3D.style.flex = '1';
      this.pane3D.style.width = 'auto';
    }

    // Schedule high-performance resize trigger
    if (!this.animationFrameId) {
      this.animationFrameId = requestAnimationFrame(() => {
        this.triggerResize();
        this.animationFrameId = null;
      });
    }
  }

  onMouseUp() {
    if (!this.activeGutter) return;
    document.body.classList.remove('resizing-active');
    if (this.gutter1) this.gutter1.classList.remove('is-dragging');
    if (this.gutter2) this.gutter2.classList.remove('is-dragging');
    this.activeGutter = null;
    this.saveLayout();
    this.triggerResize();
  }

  handleWindowResize() {
    this.triggerResize();
  }

  triggerResize() {
    if (typeof this.onResizeCallback === 'function') {
      this.onResizeCallback();
    }
  }
}

window.SplitPaneManager = SplitPaneManager;
