/**
 * app.js
 * Main UI Coordinator and Application State Manager.
 */

class App {
  constructor() {
    this.map2d = null;
    this.viewer3d = null;
    this.ulpinTools = null;

    this.wards = [];
    this.currentWardId = '1'; // Default: Ward 105 Gachibowli
    this.currentParcel = null;
    this.currentParcelsList = [];

    this.init();
  }

  async init() {
    // 1. Initialize Controllers
    this.map2d = new Map2DController('map2d', (parcelId) => this.onParcelSelected(parcelId));
    this.viewer3d = new Viewer3DController('view3d-canvas', (floorIdx, floorData) => this.onFloorSelectedIn3D(floorIdx, floorData));
    this.ulpinTools = new ULPINToolsController();

    // 2. Bind UI Events
    this.bindUIEvents();

    // 3. Load Initial Data
    await this.loadConfig();
    await this.loadStats();
    await this.loadWards();
  }

  async loadConfig() {
    try {
      const resp = await fetch('/api/config');
      if (resp.ok) {
        const config = await resp.json();
        if (config.mapbox_token) {
          this.map2d.enableMapboxTiles(config.mapbox_token);
        }
      }
    } catch (e) {
      console.warn('Could not load client config:', e);
    }
  }

  bindUIEvents() {
    // Ward dropdown change
    const wardSelect = document.getElementById('ward-select');
    if (wardSelect) {
      wardSelect.addEventListener('change', (e) => {
        this.currentWardId = e.target.value;
        this.loadWardData(this.currentWardId);
      });
    }

    // Generate Parcels button
    const generateBtn = document.getElementById('btn-generate-parcels');
    if (generateBtn) {
      generateBtn.addEventListener('click', () => this.generateParcelsForCurrentWard());
    }

    // Search bar
    const searchInput = document.getElementById('search-input');
    if (searchInput) {
      let debounceTimer = null;
      searchInput.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          this.searchParcels(e.target.value);
        }, 300);
      });
    }

    // View Mode Toggle (3D Mesh vs LiDAR)
    const btnModeMesh = document.getElementById('btn-mode-mesh');
    const btnModeLidar = document.getElementById('btn-mode-lidar');

    if (btnModeMesh && btnModeLidar) {
      btnModeMesh.addEventListener('click', () => {
        btnModeMesh.classList.add('btn-primary');
        btnModeMesh.classList.remove('btn-secondary');
        btnModeLidar.classList.add('btn-secondary');
        btnModeLidar.classList.remove('btn-primary');
        this.viewer3d.setViewMode('mesh');
      });

      btnModeLidar.addEventListener('click', () => {
        btnModeLidar.classList.add('btn-primary');
        btnModeLidar.classList.remove('btn-secondary');
        btnModeMesh.classList.add('btn-secondary');
        btnModeMesh.classList.remove('btn-primary');
        this.viewer3d.setViewMode('lidar');
      });
    }

    // Reset 3D Camera button
    const btnResetCam = document.getElementById('btn-reset-cam');
    if (btnResetCam) {
      btnResetCam.addEventListener('click', () => {
        this.viewer3d.focusCamera();
      });
    }
  }

  async loadStats() {
    try {
      const resp = await fetch('/api/stats');
      if (!resp.ok) return;
      const stats = await resp.json();

      document.getElementById('stat-wards-count').textContent = `${stats.active_wards_with_parcels} / ${stats.total_wards}`;
      document.getElementById('stat-parcels-count').textContent = stats.total_parcels.toLocaleString();
      document.getElementById('stat-units-count').textContent = stats.total_3d_units.toLocaleString();
      document.getElementById('stat-area-count').textContent = `${(stats.total_land_area_sqm / 10000).toFixed(1)} ha`;
    } catch (e) {
      console.warn('Could not load stats:', e);
    }
  }

  async loadWards() {
    try {
      const resp = await fetch('/api/wards');
      if (!resp.ok) return;
      const data = await resp.json();
      this.wards = data.wards || [];

      const select = document.getElementById('ward-select');
      select.innerHTML = '';

      this.wards.forEach(w => {
        const opt = document.createElement('option');
        opt.value = w.id;
        opt.textContent = `${w.name} ${w.parcels_count > 0 ? `(${w.parcels_count} parcels)` : ''}`;
        if (w.id === this.currentWardId) {
          opt.selected = true;
        }
        select.appendChild(opt);
      });

      // Load initial ward
      this.loadWardData(this.currentWardId);
    } catch (e) {
      console.error('Error loading wards:', e);
    }
  }

  async loadWardData(wardId) {
    try {
      // 1. Fetch Ward Geometry
      const wardResp = await fetch(`/api/wards/${wardId}`);
      if (!wardResp.ok) {
        throw new Error(`Ward request failed (${wardResp.status})`);
      }
      const wardData = await wardResp.json();
      this.map2d.setWard(wardData);

      // 2. Fetch Parcels in Ward
      const parcelsResp = await fetch(`/api/parcels?ward_id=${wardId}`);
      if (!parcelsResp.ok) {
        throw new Error(`Parcel request failed (${parcelsResp.status})`);
      }
      const parcelsGeoJSON = await parcelsResp.json();
      if (parcelsGeoJSON.type !== 'FeatureCollection') {
        throw new Error('Parcel response was not a GeoJSON FeatureCollection');
      }
      this.currentParcelsList = parcelsGeoJSON.features || [];
      this.map2d.setParcels(parcelsGeoJSON);

      // Select first parcel if available
      if (this.currentParcelsList.length > 0) {
        const firstId = this.currentParcelsList[0].properties.parcel_id;
        this.onParcelSelected(firstId);
      } else {
        this.renderEmptyInspector();
      }
    } catch (e) {
      console.error('Error loading ward data:', e);
      this.currentParcelsList = [];
      this.map2d.setParcels({ type: 'FeatureCollection', features: [] });
      this.renderEmptyInspector(`Could not load parcels: ${e.message}`);
    }
  }

  async generateParcelsForCurrentWard() {
    const btn = document.getElementById('btn-generate-parcels');
    const sourceSelect = document.getElementById('source-select');
    const source = sourceSelect ? sourceSelect.value : 'osm';

    const origText = btn.innerHTML;
    btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Fetching ${source.toUpperCase()}...`;
    btn.disabled = true;

    try {
      const resp = await fetch(`/api/wards/${this.currentWardId}/generate?count=18&source=${source}`, {
        method: 'POST'
      });

      if (!resp.ok) {
        let message = `HTTP ${resp.status}`;
        try {
          const err = await resp.json();
          message = err.detail || message;
        } catch (_) {
          // Keep the HTTP status when the server did not return JSON.
        }
        alert('Generation failed: ' + message);
        return;
      }

      await this.loadStats();
      await this.loadWards();
    } catch (e) {
      console.error(e);
      alert('Failed to trigger parcel generation.');
    } finally {
      btn.innerHTML = origText;
      btn.disabled = false;
    }
  }

  async searchParcels(term) {
    if (!term || term.trim() === '') {
      this.loadWardData(this.currentWardId);
      return;
    }

    try {
      const resp = await fetch(`/api/parcels?search=${encodeURIComponent(term)}`);
      if (resp.ok) {
        const data = await resp.json();
        this.map2d.setParcels(data);
        if (data.features && data.features.length > 0) {
          this.onParcelSelected(data.features[0].properties.parcel_id);
          this.map2d.zoomToParcel(data.features[0].properties.parcel_id);
        }
      }
    } catch (e) {
      console.error('Search error:', e);
    }
  }

  async onParcelSelected(parcelId) {
    this.map2d.selectParcel(parcelId);

    try {
      // 1. Fetch Full Parcel Data
      const pResp = await fetch(`/api/parcels/${parcelId}`);
      if (!pResp.ok) return;
      this.currentParcel = await pResp.json();

      // 2. Fetch 3D Extrusion
      const extResp = await fetch(`/api/parcels/${parcelId}/3d`);
      if (extResp.ok) {
        const extData = await extResp.json();
        this.viewer3d.setParcel3D(extData);
      }

      // 3. Fetch LiDAR Points
      const lidarResp = await fetch(`/api/parcels/${parcelId}/lidar`);
      if (lidarResp.ok) {
        const lidarData = await lidarResp.json();
        this.viewer3d.setLiDARPoints(lidarData);
      }

      // 4. Update Inspector UI
      this.renderParcelInspector(this.currentParcel);
    } catch (e) {
      console.error('Error selecting parcel:', e);
    }
  }

  onFloorSelectedIn3D(floorIdx, floorData) {
    const items = document.querySelectorAll('.floor-item');
    items.forEach(el => {
      if (parseInt(el.dataset.floorIndex, 10) === floorIdx) {
        el.classList.add('active');
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      } else {
        el.classList.remove('active');
      }
    });

    if (floorData && floorData.ulpin3D) {
      const u3d = floorData.ulpin3D.length >= 18 ? floorData.ulpin3D : `${floorData.ulpin3D}-F${floorIdx < 0 ? 'B' : ''}${Math.abs(floorIdx).toString().padStart(2, '0')}`;
      const elem3d = document.getElementById('inspector-3d-ulpin');
      if (elem3d) elem3d.textContent = u3d;
      const elemDesc = document.getElementById('inspector-3d-ulpin-desc');
      if (elemDesc) elemDesc.textContent = `3D Volumetric Unit (${floorData.floorLabel || 'Selected Unit'})`;
    }
  }

  renderParcelInspector(parcel) {
    const ext = parcel.extrusion || {};
    const buildings = ext.buildings || [];
    const mainBuilding = buildings[0] || {};
    const floors = mainBuilding.floors || [];
    const underground = ext.underground_units || [];

    const base2d = (parcel.ulpin || '832454DYJFAQY2').slice(0, 14);
    const default3d = `${base2d}-F00`;

    document.getElementById('inspector-parcel-id').textContent = parcel.parcel_id;
    
    const elem2d = document.getElementById('inspector-2d-ulpin');
    if (elem2d) elem2d.textContent = base2d;

    const elem3d = document.getElementById('inspector-3d-ulpin');
    if (elem3d) elem3d.textContent = default3d;

    document.getElementById('inspector-owner').textContent = parcel.owner_name;
    document.getElementById('inspector-survey').textContent = parcel.survey_number;
    document.getElementById('inspector-landuse').textContent = parcel.land_use;
    document.getElementById('inspector-area').textContent = `${parcel.area_sqm.toLocaleString()} m²`;

    const sourceBadge = document.getElementById('inspector-source-badge');
    if (sourceBadge) {
      sourceBadge.textContent = parcel.data_source || 'Synthetic';
      sourceBadge.style.color = (parcel.data_source && parcel.data_source.includes('OSM')) ? '#00f2fe' : '#a855f7';
    }

    document.getElementById('inspector-base-elev').textContent = `${ext.base_elevation_m || 510.0} m MSL`;
    document.getElementById('inspector-max-height').textContent = `${ext.max_height_m || 12.0} m`;
    document.getElementById('inspector-far').textContent = `${ext.far || 1.2} FAR`;
    document.getElementById('inspector-units-count').textContent = `${ext.total_units_count || (floors.length + underground.length)} Units`;

    // Render floor & underground units list
    const floorListContainer = document.getElementById('inspector-floor-list');
    floorListContainer.innerHTML = '';

    // 1. Above Ground Floors
    floors.forEach(fl => {
      const item = document.createElement('div');
      item.className = 'floor-item';
      item.dataset.floorIndex = fl.floor_index;
      
      const u3d = fl.ulpin_3d && fl.ulpin_3d.length >= 18 ? fl.ulpin_3d : `${base2d}-F${fl.floor_index.toString().padStart(2, '0')}`;

      item.innerHTML = `
        <div class="floor-left">
          <div class="floor-tag">${fl.floor_label}</div>
          <div class="floor-meta">
            <div class="floor-name">${fl.unit_type || 'Apartment Unit'}</div>
            <div class="floor-elev">Z: ${fl.z_min}m - ${fl.z_max}m (${fl.height}m)</div>
          </div>
        </div>
        <div class="floor-ulpin-tag" style="font-family: var(--font-mono); font-size: 11px; color: #a855f7;">${u3d}</div>
      `;

      item.addEventListener('click', () => {
        const isCurrentActive = item.classList.contains('active');
        if (isCurrentActive) {
          this.viewer3d.isolateFloor(null);
          document.querySelectorAll('.floor-item').forEach(el => el.classList.remove('active'));
          if (elem3d) elem3d.textContent = default3d;
          const elemDesc = document.getElementById('inspector-3d-ulpin-desc');
          if (elemDesc) elemDesc.textContent = '3D Volumetric ULPIN (Ground Level)';
        } else {
          document.querySelectorAll('.floor-item').forEach(el => el.classList.remove('active'));
          item.classList.add('active');
          this.viewer3d.isolateFloor(fl.floor_index);
          if (elem3d) elem3d.textContent = u3d;
          const elemDesc = document.getElementById('inspector-3d-ulpin-desc');
          if (elemDesc) elemDesc.textContent = `3D Volumetric Unit (${fl.floor_label})`;
        }
      });

      floorListContainer.appendChild(item);
    });

    // 2. Underground Units (Basements & Utility Conduit)
    underground.forEach(u => {
      const item = document.createElement('div');
      item.className = 'floor-item';
      item.style.borderColor = 'rgba(16, 185, 129, 0.3)';
      item.style.background = 'rgba(16, 185, 129, 0.05)';

      item.innerHTML = `
        <div class="floor-left">
          <div class="floor-tag" style="background: rgba(16, 185, 129, 0.2); color: #10b981;">${u.unit_id.split('-').pop()}</div>
          <div class="floor-meta">
            <div class="floor-name" style="color: #6ee7b7;">${u.label}</div>
            <div class="floor-elev">Z: ${u.z_min}m - ${u.z_max}m (${u.depth_m}m depth)</div>
          </div>
        </div>
        <div class="floor-ulpin-tag" style="font-family: var(--font-mono); font-size: 11px; color: #10b981;">${u.ulpin_3d}</div>
      `;

      item.addEventListener('click', () => {
        document.querySelectorAll('.floor-item').forEach(el => el.classList.remove('active'));
        item.classList.add('active');
        if (elem3d) elem3d.textContent = u.ulpin_3d;
        const elemDesc = document.getElementById('inspector-3d-ulpin-desc');
        if (elemDesc) elemDesc.textContent = `Sub-Surface 3D Unit (${u.category})`;
      });

      floorListContainer.appendChild(item);
    });
  }

  renderEmptyInspector(message = 'No parcels in this ward. Click "Generate 3D Parcels" above.') {
    document.getElementById('inspector-parcel-id').textContent = 'No Parcel Selected';
    const elem2d = document.getElementById('inspector-2d-ulpin');
    if (elem2d) elem2d.textContent = '--------------';
    const elem3d = document.getElementById('inspector-3d-ulpin');
    if (elem3d) elem3d.textContent = '------------------';
    document.getElementById('inspector-owner').textContent = '—';
    document.getElementById('inspector-survey').textContent = '—';
    document.getElementById('inspector-landuse').textContent = '—';
    document.getElementById('inspector-area').textContent = '—';
    document.getElementById('inspector-floor-list').innerHTML = `<div style="color: #64748b; font-size: 12px; text-align: center; padding: 20px;">${message}</div>`;
  }

}

// Instantiate on DOM load
window.addEventListener('DOMContentLoaded', () => {
  window.app = new App();
});
