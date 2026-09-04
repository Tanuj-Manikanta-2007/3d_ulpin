/**
 * viewer3d.js
 * Three.js 3D Digital Twin & Cadastral Volumetric Viewer.
 * Features:
 * - Volumetric building extrusion with multi-story floor slicing
 * - Interactive floor-level selection & highlight
 * - Synthetic LiDAR point cloud visualization mode (ASPRS classified)
 * - OrbitControls and smooth camera transitions
 */

class Viewer3DController {
  constructor(canvasId, onFloorSelected) {
    this.canvas = document.getElementById(canvasId);
    this.onFloorSelected = onFloorSelected;

    this.scene = null;
    this.camera = null;
    this.renderer = null;
    this.controls = null;
    this.raycaster = new THREE.Raycaster();
    this.mouse = new THREE.Vector2();

    this.buildingGroup = new THREE.Group();
    this.lidarGroup = new THREE.Group();
    this.groundPlane = null;
    this.gridHelper = null;

    this.currentParcel3D = null;
    this.activeFloorIndex = null; // null = all floors visible
    this.viewMode = 'mesh'; // 'mesh' or 'lidar'
    this.floorMeshes = [];

    this.initThree();
  }

  initThree() {
    const width = this.canvas.parentElement.clientWidth;
    const height = this.canvas.parentElement.clientHeight;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0a0e1a);
    this.scene.fog = new THREE.FogExp2(0x0a0e1a, 0.005);

    this.camera = new THREE.PerspectiveCamera(45, width / height, 0.5, 2000);
    this.camera.position.set(60, 45, 60);

    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      alpha: true
    });
    this.renderer.setSize(width, height);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;

    this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.05;
    this.controls.maxPolarAngle = Math.PI / 2 - 0.05; // don't go below ground
    this.controls.target.set(0, 5, 0);

    this.setupLighting();
    this.setupEnvironment();

    this.scene.add(this.buildingGroup);
    this.scene.add(this.lidarGroup);

    window.addEventListener('resize', () => this.onWindowResize());
    if (window.ResizeObserver && this.canvas.parentElement) {
      new ResizeObserver(() => this.onWindowResize()).observe(this.canvas.parentElement);
    }

    this.canvas.addEventListener('click', (e) => this.onCanvasClick(e));

    this.animate();
  }


  setupLighting() {
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    this.scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0x00f2fe, 1.2);
    dirLight.position.set(50, 80, 40);
    dirLight.castShadow = true;
    this.scene.add(dirLight);

    const secondaryLight = new THREE.DirectionalLight(0x8b5cf6, 0.8);
    secondaryLight.position.set(-40, 40, -40);
    this.scene.add(secondaryLight);
  }

  setupEnvironment() {
    // Grid ground helper
    this.gridHelper = new THREE.GridHelper(200, 40, 0x00f2fe, 0x1e293b);
    this.gridHelper.position.y = 0;
    this.scene.add(this.gridHelper);

    // Subtle terrain base plane
    const groundGeo = new THREE.PlaneGeometry(200, 200);
    const groundMat = new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      roughness: 0.9,
      metalness: 0.1
    });
    this.groundPlane = new THREE.Mesh(groundGeo, groundMat);
    this.groundPlane.rotation.x = -Math.PI / 2;
    this.groundPlane.position.y = -0.05;
    this.groundPlane.receiveShadow = true;
    this.scene.add(this.groundPlane);
  }

  onWindowResize() {
    if (!this.canvas || !this.canvas.parentElement) return;
    const width = this.canvas.parentElement.clientWidth;
    const height = this.canvas.parentElement.clientHeight;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height);
  }

  clearScene() {
    while (this.buildingGroup.children.length > 0) {
      const obj = this.buildingGroup.children[0];
      if (obj.geometry) obj.geometry.dispose();
      this.buildingGroup.remove(obj);
    }
    while (this.lidarGroup.children.length > 0) {
      const obj = this.lidarGroup.children[0];
      if (obj.geometry) obj.geometry.dispose();
      this.lidarGroup.remove(obj);
    }
    this.floorMeshes = [];
    this.currentParcel3D = null;
    this.activeFloorIndex = null;
  }

  setParcel3D(extrusionData) {
    this.clearScene();
    if (!extrusionData || !extrusionData.buildings) return;

    this.currentParcel3D = extrusionData;
    const baseElev = extrusionData.base_elevation_m || 510.0;

    extrusionData.buildings.forEach((bldg, bIdx) => {
      const floors = bldg.floors || [];
      const floorMeshes = bldg.floor_meshes || [];

      floorMeshes.forEach((fm, fIdx) => {
        const meshData = fm.mesh;
        if (!meshData || !meshData.vertices || meshData.vertices.length === 0) return;

        // Construct BufferGeometry
        const geometry = new THREE.BufferGeometry();
        const positions = [];
        const indices = [];

        // Vertices normalized to relative Y from baseElev
        meshData.vertices.forEach(v => {
          positions.push(v[0], v[1] - baseElev, v[2]);
        });

        meshData.faces.forEach(f => {
          indices.push(f[0], f[1], f[2]);
        });

        geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
        geometry.setIndex(indices);
        geometry.computeVertexNormals();

        // Floor Unit Material (Glassmorphic glow)
        const isGround = fIdx === 0;
        const matColor = isGround ? 0x00f2fe : (fIdx % 2 === 0 ? 0x38bdf8 : 0x818cf8);
        
        const material = new THREE.MeshPhysicalMaterial({
          color: matColor,
          transparent: true,
          opacity: 0.75,
          roughness: 0.2,
          metalness: 0.1,
          transmission: 0.4,
          ior: 1.4,
          side: THREE.DoubleSide
        });

        const mesh = new THREE.Mesh(geometry, material);
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        mesh.userData = {
          floorIndex: fm.floor_index,
          floorLabel: fm.floor_label,
          ulpin3D: fm.ulpin_3d,
          buildingId: bldg.building_id,
          originalColor: matColor
        };

        // Wireframe edges
        const edges = new THREE.EdgesGeometry(geometry);
        const lineMat = new THREE.LineBasicMaterial({ color: 0xffffff, linewidth: 1, transparent: true, opacity: 0.4 });
        const wireframe = new THREE.LineSegments(edges, lineMat);
        mesh.add(wireframe);

        this.buildingGroup.add(mesh);
        this.floorMeshes.push(mesh);
      });
    });

    this.setViewMode(this.viewMode);
    this.focusCamera();
  }

  setLiDARPoints(pointsData) {
    while (this.lidarGroup.children.length > 0) {
      const obj = this.lidarGroup.children[0];
      if (obj.geometry) obj.geometry.dispose();
      this.lidarGroup.remove(obj);
    }

    if (!pointsData || !pointsData.points || pointsData.points.length === 0) return;

    const baseElev = this.currentParcel3D ? this.currentParcel3D.base_elevation_m : 510.0;
    const geometry = new THREE.BufferGeometry();
    const positions = [];
    const colors = [];

    const colorPalette = {
      2: new THREE.Color(0x22c55e), // Ground - Green
      5: new THREE.Color(0x10b981), // Vegetation - Emerald
      6: new THREE.Color(0x00f2fe), // Building - Cyan
      'default': new THREE.Color(0xf59e0b) // Other - Gold
    };

    pointsData.points.forEach(pt => {
      positions.push(pt.x, pt.y - baseElev, pt.z);
      const col = colorPalette[pt.classification] || colorPalette.default;
      colors.push(col.r, col.g, col.b);
    });

    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));

    const material = new THREE.PointsMaterial({
      size: 0.45,
      vertexColors: true,
      transparent: true,
      opacity: 0.85
    });

    const pointCloud = new THREE.Points(geometry, material);
    this.lidarGroup.add(pointCloud);
  }

  setViewMode(mode) {
    this.viewMode = mode;
    if (mode === 'lidar') {
      this.buildingGroup.visible = false;
      this.lidarGroup.visible = true;
    } else {
      this.buildingGroup.visible = true;
      this.lidarGroup.visible = false;
    }
  }

  isolateFloor(floorIndex) {
    this.activeFloorIndex = floorIndex;

    this.floorMeshes.forEach(mesh => {
      if (floorIndex === null || floorIndex === undefined) {
        // Show all floors
        mesh.visible = true;
        mesh.material.opacity = 0.75;
        mesh.position.y = 0;
      } else if (mesh.userData.floorIndex === floorIndex) {
        // Active floor highlighted
        mesh.visible = true;
        mesh.material.opacity = 0.95;
        mesh.material.color.setHex(0x00f2fe);
      } else {
        // Other floors dimmed or hidden
        mesh.visible = true;
        mesh.material.opacity = 0.15;
        mesh.material.color.setHex(0x475569);
      }
    });
  }

  focusCamera() {
    const box = new THREE.Box3().setFromObject(this.buildingGroup);
    if (!box.isEmpty()) {
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z, 20);

      this.controls.target.copy(center);
      this.camera.position.set(center.x + maxDim * 1.6, center.y + maxDim * 1.2, center.z + maxDim * 1.6);
      this.controls.update();
    }
  }

  onCanvasClick(event) {
    const rect = this.canvas.getBoundingClientRect();
    this.mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    this.raycaster.setFromCamera(this.mouse, this.camera);
    const intersects = this.raycaster.intersectObjects(this.floorMeshes);

    if (intersects.length > 0) {
      const hit = intersects[0].object;
      const fIdx = hit.userData.floorIndex;
      this.isolateFloor(fIdx);
      if (this.onFloorSelected) {
        this.onFloorSelected(fIdx, hit.userData);
      }
    }
  }

  animate() {
    requestAnimationFrame(() => this.animate());
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
