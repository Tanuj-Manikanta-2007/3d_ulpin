/**
 * app.js
 * Main UI Coordinator and Application State Manager.
 */

// Resilient fetch wrapper that bypasses buggy browser extensions (e.g. 200.js / requests.js)
// which monkey-patch window.fetch and throw unhandled errors like "Cannot read properties of undefined (reading 'M_ID')".
const safeFetch = (() => {
  let cleanFetch = null;
  try {
    const iframe = document.createElement('iframe');
    iframe.style.display = 'none';
    document.documentElement.appendChild(iframe);
    if (iframe.contentWindow && iframe.contentWindow.fetch) {
      cleanFetch = iframe.contentWindow.fetch.bind(window);
    }
    document.documentElement.removeChild(iframe);
  } catch (_) {
    cleanFetch = null;
  }

  return async function(url, options) {
    if (cleanFetch) {
      try {
        return await cleanFetch(url, options);
      } catch (_) {
        // Fall back to window.fetch or XHR
      }
    }

    try {
      return await window.fetch(url, options);
    } catch (fetchErr) {
      // Fallback to native XMLHttpRequest if extension monkey-patch corrupted fetch
      return new Promise((resolve, reject) => {
        try {
          const xhr = new XMLHttpRequest();
          const method = (options && options.method) || 'GET';
          xhr.open(method, url, true);
          if (options && options.headers) {
            for (const [k, v] of Object.entries(options.headers)) {
              xhr.setRequestHeader(k, v);
            }
          }
          xhr.onload = () => {
            resolve({
              ok: xhr.status >= 200 && xhr.status < 300,
              status: xhr.status,
              statusText: xhr.statusText,
              json: async () => JSON.parse(xhr.responseText),
              text: async () => xhr.responseText
            });
          };
          xhr.onerror = () => reject(new TypeError('Network request failed'));
          xhr.send((options && options.body) || null);
        } catch (xhrErr) {
          reject(xhrErr);
        }
      });
    }
  };
})();

window.safeFetch = safeFetch;

class App {
  constructor() {
    this.map2d = null;
    this.viewer3d = null;
    this.ulpinTools = null;

    this.states = [];
    this.cities = [];
    this.wards = [];

    this.currentStateCode = 'TS'; // Default: Telangana
    this.currentCityId = 'TS-HYD'; // Default: Hyderabad (GHMC)
    this.currentWardId = '1';      // Default: Ward 105 Gachibowli
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
    await this.loadHierarchy();
  }

  async loadConfig() {
    try {
      const resp = await safeFetch('/api/config');
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
    // State dropdown change
    const stateSelect = document.getElementById('state-select');
    if (stateSelect) {
      stateSelect.addEventListener('change', (e) => this.onStateChanged(e.target.value));
    }

    // City dropdown change
    const citySelect = document.getElementById('city-select');
    if (citySelect) {
      citySelect.addEventListener('change', (e) => this.onCityChanged(e.target.value));
    }

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

    // Free 360 Auto-Rotate button
    const btnAutoRotate = document.getElementById('btn-autorotate-3d');
    if (btnAutoRotate) {
      btnAutoRotate.addEventListener('click', () => {
        const isRotating = this.viewer3d.toggleAutoRotate();
        if (isRotating) {
          btnAutoRotate.classList.add('btn-primary');
          btnAutoRotate.classList.remove('btn-secondary');
        } else {
          btnAutoRotate.classList.add('btn-secondary');
          btnAutoRotate.classList.remove('btn-primary');
        }
      });
    }
  }

  async loadStats() {
    try {
      const resp = await safeFetch('/api/stats');
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

  async loadHierarchy() {
    try {
      const resp = await safeFetch('/api/states');
      if (!resp.ok) return;
      const data = await resp.json();
      this.states = data.states || [];

      const stateSelect = document.getElementById('state-select');
      if (stateSelect) {
        stateSelect.innerHTML = '';
        this.states.forEach(s => {
          const opt = document.createElement('option');
          opt.value = s.state_code;
          opt.textContent = `${s.state_name} (${s.cities_count} cities)`;
          if (s.state_code === this.currentStateCode) {
            opt.selected = true;
          }
          stateSelect.appendChild(opt);
        });
      }

      await this.loadCities(this.currentStateCode);
    } catch (e) {
      console.error('Error loading states:', e);
    }
  }

  async loadCities(stateCode) {
    try {
      const resp = await safeFetch(`/api/states/${stateCode}/cities`);
      if (!resp.ok) return;
      const data = await resp.json();
      this.cities = data.cities || [];

      const citySelect = document.getElementById('city-select');
      if (citySelect) {
        citySelect.innerHTML = '';
        this.cities.forEach(c => {
          const opt = document.createElement('option');
          opt.value = c.city_id;
          opt.textContent = `${c.city_name} (${c.wards_count} wards)`;
          if (c.city_id === this.currentCityId) {
            opt.selected = true;
          }
          citySelect.appendChild(opt);
        });

        // Ensure currentCityId is valid for selected state
        if (!this.cities.some(c => c.city_id === this.currentCityId) && this.cities.length > 0) {
          this.currentCityId = this.cities[0].city_id;
          citySelect.value = this.currentCityId;
        }
      }

      await this.loadWards(this.currentCityId);
    } catch (e) {
      console.error('Error loading cities:', e);
    }
  }

  async loadWards(cityId, reloadData = true) {
    try {
      const resp = await safeFetch(`/api/cities/${cityId}/wards`);
      if (!resp.ok) return;
      const data = await resp.json();
      this.wards = data.wards || [];

      const wardSelect = document.getElementById('ward-select');
      if (wardSelect) {
        wardSelect.innerHTML = '';
        this.wards.forEach(w => {
          const opt = document.createElement('option');
          opt.value = w.id;
          opt.textContent = `${w.name} ${w.parcels_count > 0 ? `(${w.parcels_count} parcels)` : ''}`;
          if (String(w.id) === String(this.currentWardId)) {
            opt.selected = true;
          }
          wardSelect.appendChild(opt);
        });

        // Ensure currentWardId is valid for selected city
        if (!this.wards.some(w => String(w.id) === String(this.currentWardId)) && this.wards.length > 0) {
          this.currentWardId = this.wards[0].id;
          wardSelect.value = this.currentWardId;
        }
      }

      if (reloadData && this.currentWardId) {
        await this.loadWardData(this.currentWardId);
      }
    } catch (e) {
      console.error('Error loading wards:', e);
    }
  }

  async onStateChanged(stateCode) {
    this.currentStateCode = stateCode;
    const st = this.states.find(s => s.state_code === stateCode);
    if (st && st.center && this.map2d.map) {
      this.map2d.map.flyTo(st.center, 8, { duration: 1.2 });
    }
    await this.loadCities(stateCode);
  }

  async onCityChanged(cityId) {
    this.currentCityId = cityId;
    const ct = this.cities.find(c => c.city_id === cityId);
    if (ct && ct.center && this.map2d.map) {
      this.map2d.map.flyTo(ct.center, 12, { duration: 1.2 });
    }
    await this.loadWards(cityId);
  }

  async loadWardData(wardId) {
    try {
      this.renderEmptyInspector('<i class="fas fa-spinner fa-spin" style="color: #00f2fe; margin-right: 6px;"></i> Loading 3D Parcels from Database...');

      // 1. Fetch Ward Geometry
      const wardResp = await safeFetch(`/api/wards/${wardId}`);
      if (!wardResp.ok) {
        throw new Error(`Ward request failed (${wardResp.status})`);
      }
      const wardData = await wardResp.json();
      this.map2d.setWard(wardData);

      // 2. Fetch Parcels in Ward (DB-First: returned immediately from DB if present, or generated via OSM & saved)
      const sourceSelect = document.getElementById('source-select');
      const source = sourceSelect ? sourceSelect.value : 'osm';

      const parcelsResp = await safeFetch(`/api/parcels?ward_id=${wardId}&source=${source}`);
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

      await this.loadStats();
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
    btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Generating All Parcels (${source.toUpperCase()})...`;
    btn.disabled = true;

    try {
<<<<<<< HEAD
      const resp = await fetch(`/api/wards/${this.currentWardId}/generate?source=${source}`, {
=======
      const resp = await safeFetch(`/api/wards/${this.currentWardId}/generate?source=${source}`, {
>>>>>>> d674a2a5c7876f346ceac71627e5a12456fc5451
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

      const genData = await resp.json();

      // Render all generated parcels across the ward location directly on 2D map
      if (genData.parcels && genData.parcels.length > 0) {
        const features = genData.parcels.map(p => ({
          type: "Feature",
          id: p.parcel_id,
          properties: {
            parcel_id: p.parcel_id,
            ward_id: p.ward_id,
            ulpin: p.ulpin,
            survey_number: p.survey_number,
            land_use: p.land_use,
            owner_name: p.owner_name,
            area_sqm: p.area_sqm,
            buildings_count: p.buildings_count,
            floors_count: p.floors_count,
            data_source: p.data_source || 'OpenStreetMap Live',
            is_persisted_to_db: p.is_persisted_to_db,
            centroid: p.centroid
          },
          geometry: p.geometry
        }));

        this.currentParcelsList = features;
        this.map2d.setParcels({ type: "FeatureCollection", features: features });

        if (features.length > 0) {
          const firstId = features[0].properties.parcel_id;
          this.onParcelSelected(firstId);
        }
      }

      await this.loadStats();
      await this.loadWards(this.currentCityId, false);
    } catch (e) {
      console.error(e);
      alert('Failed to trigger parcel generation: ' + (e.message || e));
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
      const resp = await safeFetch(`/api/parcels?search=${encodeURIComponent(term)}`);
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
      // 1. Fetch full parcel metadata
      try {
        const pResp = await safeFetch(`/api/parcels/${parcelId}`);
        if (pResp && pResp.ok) {
          this.currentParcel = await pResp.json();
          this.renderParcelInspector(this.currentParcel);
        }
      } catch (pErr) {
        console.warn('Could not load parcel metadata:', pErr);
      }

      // 2. Fetch 3D extrusion model
      try {
        const extResp = await safeFetch(`/api/parcels/${parcelId}/3d`);
        if (extResp && extResp.ok) {
          const extData = await extResp.json();
          this.viewer3d.setParcel3D(extData);
        }
      } catch (extErr) {
        console.warn('Could not load 3D extrusion:', extErr);
      }

      // 3. Fetch LiDAR point cloud
      try {
        const lidarResp = await safeFetch(`/api/parcels/${parcelId}/lidar`);
        if (lidarResp && lidarResp.ok) {
          const lidarData = await lidarResp.json();
          this.viewer3d.setLiDARPoints(lidarData);
        }
      } catch (lidarErr) {
        console.warn('Could not load LiDAR points:', lidarErr);
      }
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
