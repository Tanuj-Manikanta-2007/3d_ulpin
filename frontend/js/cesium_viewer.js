/**
 * cesium_viewer.js
 * CesiumJS 3D Geospatial Digital Twin Viewer.
 * Renders 3D Building polyhedra, terrain elevation, and cadastral layers in global context.
 */

class CesiumViewerController {
  constructor(containerId, onEntitySelected) {
    this.containerId = containerId;
    this.onEntitySelected = onEntitySelected;
    this.viewer = null;
    this.isInitialized = false;
    this.currentEntities = [];
  }

  init() {
    if (this.isInitialized || !window.Cesium) return;

    try {
      // Disable default Cesium Ion token prompt for open-source basemaps
      Cesium.Ion.defaultAccessToken = "";

      this.viewer = new Cesium.Viewer(this.containerId, {
        terrainProvider: new Cesium.EllipsoidTerrainProvider(),
        imageryProvider: new Cesium.UrlTemplateImageryProvider({
          url: 'https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png',
          subdomains: ['a', 'b', 'c', 'd']
        }),
        baseLayerPicker: false,
        geocoder: false,
        homeButton: false,
        infoBox: false,
        sceneModePicker: false,
        selectionIndicator: false,
        navigationHelpButton: false,
        animation: false,
        timeline: false,
        fullscreenButton: false
      });

      // Camera view over Hyderabad (Lat: 17.44, Lon: 78.38)
      this.viewer.camera.setView({
        destination: Cesium.Cartesian3.fromDegrees(78.3800, 17.4400, 1800),
        orientation: {
          heading: Cesium.Math.toRadians(0.0),
          pitch: Cesium.Math.toRadians(-45.0),
          roll: 0.0
        }
      });

      // Click handler for 3D buildings
      const handler = new Cesium.ScreenSpaceEventHandler(this.viewer.scene.canvas);
      handler.setInputAction((movement) => {
        const pickedObject = this.viewer.scene.pick(movement.position);
        if (Cesium.defined(pickedObject) && pickedObject.id && pickedObject.id.properties) {
          const props = pickedObject.id.properties;
          if (this.onEntitySelected) {
            this.onEntitySelected(props.getValue());
          }
        }
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

      this.isInitialized = true;
      console.log("[CesiumJS] Digital Twin 3D Globe initialized successfully.");
    } catch (e) {
      console.warn("[CesiumJS] Note on Cesium initialization:", e);
    }
  }

  show() {
    const container = document.getElementById(this.containerId);
    if (container) container.style.display = "block";
    if (!this.isInitialized) {
      this.init();
    }
  }

  hide() {
    const container = document.getElementById(this.containerId);
    if (container) container.style.display = "none";
  }

  render3DBuilding(tiles3dData) {
    if (!this.viewer || !tiles3dData || !tiles3dData.features) return;

    // Clear previous entities
    this.currentEntities.forEach(ent => this.viewer.entities.remove(ent));
    this.currentEntities = [];

    const baseElev = tiles3dData.base_elevation_msl || 512.0;

    tiles3dData.features.forEach(feat => {
      const geom = feat.geometry;
      const props = feat.properties || {};
      if (!geom || geom.type !== "Polygon") return;

      const coords = geom.coordinates[0];
      const flatCoords = [];
      coords.forEach(pt => {
        flatCoords.push(pt[0], pt[1]);
      });

      const zMin = (props.z_min || baseElev);
      const zMax = (props.z_max || zMin + 3.2);

      const colorHex = props.color || "#00f2fe";
      const cesiumColor = Cesium.Color.fromCssColorString(colorHex).withAlpha(0.85);

      const entity = this.viewer.entities.add({
        name: props.floor_label || "Floor Unit",
        polygon: {
          hierarchy: Cesium.Cartesian3.fromDegreesArray(flatCoords),
          height: zMin,
          extrudedHeight: zMax,
          material: cesiumColor,
          outline: true,
          outlineColor: Cesium.Color.WHITE.withAlpha(0.9)
        },
        properties: {
          floor_label: props.floor_label,
          ulpin_3d: props.ulpin_3d,
          unit_type: props.unit_type,
          height: zMax - zMin,
          parcel_id: tiles3dData.parcel_id
        }
      });

      this.currentEntities.push(entity);
    });

    // Fly camera to the building
    if (this.currentEntities.length > 0) {
      this.viewer.zoomTo(this.currentEntities);
    }
  }
}
