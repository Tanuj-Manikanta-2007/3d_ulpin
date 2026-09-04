/**
 * map2d.js
 * Leaflet 2D Cadastral Map Controller.
 * Features:
 * - 100% Free, no-API-key tile layers (Dark Matter, OpenStreetMap, Satellite)
 * - Layer control switcher
 * - Accurate parcel styling by land-use
 * - Ward boundary outline & interactive selection
 */

class Map2DController {
  constructor(containerId, onParcelSelected) {
    this.containerId = containerId;
    this.onParcelSelected = onParcelSelected;
    this.map = null;
    this.wardLayer = null;
    this.parcelsLayer = null;
    this.selectedParcelId = null;

    this.landUseColors = {
      'Residential': '#3b82f6',   // Blue
      'Commercial': '#f59e0b',    // Amber
      'Mixed Use': '#a855f7',     // Purple
      'Institutional': '#10b981', // Emerald
      'Default': '#00f2fe'
    };

    this.initMap();
  }

  initMap() {
    // Center on Hyderabad coordinates (Lat: 17.44, Lon: 78.38)
    this.map = L.map(this.containerId, {
      zoomControl: false,
      attributionControl: false
    }).setView([17.4400, 78.3800], 13);

    // Free, reliable tile layers with zero API key required
    const darkLayer = L.tileLayer('https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png', {
      maxZoom: 19,
      subdomains: 'abcd',
      attribution: 'CartoDB'
    });

    const osmLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: 'OpenStreetMap'
    });

    const satelliteLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 19,
      attribution: 'Esri World Imagery'
    });

    // Default to Dark Matter
    darkLayer.addTo(this.map);

    this.baseMaps = {
      "Dark Matter (Free)": darkLayer,
      "OpenStreetMap (Free)": osmLayer,
      "Satellite Imagery (Esri)": satelliteLayer
    };

    this.layerControl = L.control.layers(this.baseMaps, null, { position: 'bottomleft' }).addTo(this.map);

    L.control.zoom({ position: 'bottomright' }).addTo(this.map);

    this.wardLayer = L.geoJSON(null, {
      style: {
        color: '#00f2fe',
        weight: 2.5,
        dashArray: '5, 8',
        fillOpacity: 0.05,
        fillColor: '#00f2fe'
      }
    }).addTo(this.map);

    this.parcelsLayer = L.geoJSON(null, {
      style: (feature) => this.getParcelStyle(feature),
      onEachFeature: (feature, layer) => this.bindParcelEvents(feature, layer)
    }).addTo(this.map);
  }

  enableMapboxTiles(mapboxToken) {
    if (!mapboxToken) return;

    const mapboxDark = L.tileLayer(`https://api.mapbox.com/styles/v1/mapbox/dark-v11/tiles/{z}/{x}/{y}?access_token=${mapboxToken}`, {
      maxZoom: 22,
      tileSize: 512,
      zoomOffset: -1,
      attribution: '© Mapbox'
    });

    const mapboxSatellite = L.tileLayer(`https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12/tiles/{z}/{x}/{y}?access_token=${mapboxToken}`, {
      maxZoom: 22,
      tileSize: 512,
      zoomOffset: -1,
      attribution: '© Mapbox'
    });

    const mapboxStreets = L.tileLayer(`https://api.mapbox.com/styles/v1/mapbox/streets-v12/tiles/{z}/{x}/{y}?access_token=${mapboxToken}`, {
      maxZoom: 22,
      tileSize: 512,
      zoomOffset: -1,
      attribution: '© Mapbox'
    });

    this.layerControl.addBaseLayer(mapboxDark, "Mapbox Dark (HD)");
    this.layerControl.addBaseLayer(mapboxSatellite, "Mapbox Satellite HD");
    this.layerControl.addBaseLayer(mapboxStreets, "Mapbox Streets HD");
  }

  getParcelStyle(feature) {
    const props = feature.properties || {};
    const landUse = props.land_use || 'Default';
    const isSelected = props.parcel_id === this.selectedParcelId;
    const baseColor = this.landUseColors[landUse] || this.landUseColors.Default;

    return {
      color: isSelected ? '#00f2fe' : '#ffffff',
      weight: isSelected ? 3.5 : 1.5,
      opacity: isSelected ? 1.0 : 0.8,
      fillColor: baseColor,
      fillOpacity: isSelected ? 0.75 : 0.45
    };
  }

  bindParcelEvents(feature, layer) {
    const props = feature.properties || {};
    
    layer.bindTooltip(`
      <div style="font-family: 'Outfit', sans-serif; font-size: 12px; color: #fff; line-height: 1.4;">
        <strong style="color: #00f2fe;">${props.parcel_id}</strong><br>
        <span style="font-family: monospace; font-size: 11px; color: #94a3b8;">${props.ulpin || ''}</span><br>
        <span>${props.land_use} • ${props.floors_count || 1} Floors</span><br>
        <span style="font-size: 10px; color: #a855f7;">${props.data_source || ''}</span>
      </div>
    `, { sticky: true, className: 'leaflet-dark-tooltip' });

    layer.on({
      mouseover: (e) => {
        if (props.parcel_id !== this.selectedParcelId) {
          e.target.setStyle({ fillOpacity: 0.7, weight: 2.5 });
        }
      },
      mouseout: (e) => {
        if (props.parcel_id !== this.selectedParcelId) {
          this.parcelsLayer.resetStyle(e.target);
        }
      },
      click: (e) => {
        L.DomEvent.stopPropagation(e);
        this.selectParcel(props.parcel_id);
        if (this.onParcelSelected) {
          this.onParcelSelected(props.parcel_id);
        }
      }
    });
  }

  setWard(wardData) {
    this.wardLayer.clearLayers();
    if (wardData && wardData.geometry) {
      this.wardLayer.addData(wardData.geometry);
      const bounds = this.wardLayer.getBounds();
      if (bounds.isValid()) {
        this.map.fitBounds(bounds, { padding: [25, 25] });
      }
    }
  }

  setParcels(geojson) {
    this.parcelsLayer.clearLayers();
    if (geojson && geojson.features && geojson.features.length > 0) {
      this.parcelsLayer.addData(geojson);
      const bounds = this.parcelsLayer.getBounds();
      if (bounds.isValid()) {
        this.map.fitBounds(bounds, { padding: [30, 30] });
      }
    }
  }

  selectParcel(parcelId) {
    this.selectedParcelId = parcelId;
    this.parcelsLayer.eachLayer((layer) => {
      const props = layer.feature.properties || {};
      layer.setStyle(this.getParcelStyle(layer.feature));
      if (props.parcel_id === parcelId) {
        layer.bringToFront();
      }
    });
  }

  zoomToParcel(parcelId) {
    this.parcelsLayer.eachLayer((layer) => {
      if (layer.feature && layer.feature.properties && layer.feature.properties.parcel_id === parcelId) {
        this.map.fitBounds(layer.getBounds(), { maxZoom: 18, padding: [40, 40] });
      }
    });
  }
}
