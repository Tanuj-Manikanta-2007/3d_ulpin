/**
 * ulpin_tools.js
 * Modal controller for interactive DoLR/NIC 3D ULPIN encoding & reverse decoding.
 */

class ULPINToolsController {
  constructor() {
    this.modal = document.getElementById('ulpin-modal');
    this.encodeTab = document.getElementById('tab-encode');
    this.decodeTab = document.getElementById('tab-decode');
    
    this.encodeForm = document.getElementById('encode-form');
    this.decodeForm = document.getElementById('decode-form');
    
    this.breakdownContainer = document.getElementById('ulpin-breakdown-result');
    this.decodeResultContainer = document.getElementById('ulpin-decode-result');

    this.bindEvents();
  }

  bindEvents() {
    const openBtn = document.getElementById('btn-open-ulpin-tool');
    const closeBtn = document.getElementById('btn-close-modal');

    if (openBtn) {
      openBtn.addEventListener('click', () => this.openModal());
    }
    if (closeBtn) {
      closeBtn.addEventListener('click', () => this.closeModal());
    }

    if (this.modal) {
      this.modal.addEventListener('click', (e) => {
        if (e.target === this.modal) this.closeModal();
      });
    }

    const calcBtn = document.getElementById('btn-calculate-ulpin');
    if (calcBtn) {
      calcBtn.addEventListener('click', () => this.performEncoding());
    }

    const decBtn = document.getElementById('btn-decode-ulpin');
    if (decBtn) {
      decBtn.addEventListener('click', () => this.performDecoding());
    }
  }

  openModal(lat = null, lon = null, floor = 0) {
    if (lat !== null && lon !== null) {
      const inputLat = document.getElementById('input-lat');
      const inputLon = document.getElementById('input-lon');
      const inputFloor = document.getElementById('input-floor');
      if (inputLat) inputLat.value = lat;
      if (inputLon) inputLon.value = lon;
      if (inputFloor) inputFloor.value = floor;
      this.performEncoding();
    }
    if (this.modal) {
      this.modal.classList.add('active');
    }
  }

  closeModal() {
    if (this.modal) {
      this.modal.classList.remove('active');
    }
  }

  async performEncoding() {
    const inputLat = document.getElementById('input-lat');
    const inputLon = document.getElementById('input-lon');
    const inputFloor = document.getElementById('input-floor');
    
    if (!inputLat || !inputLon) return;

    const lat = parseFloat(inputLat.value);
    const lon = parseFloat(inputLon.value);
    const floor = parseInt(inputFloor ? inputFloor.value || '0' : '0', 10);

    if (isNaN(lat) || isNaN(lon)) {
      alert('Please enter valid latitude and longitude coordinates.');
      return;
    }

    try {
      const resp = await fetch('/api/ulpin/encode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ latitude: lat, longitude: lon, floor: floor })
      });

      if (!resp.ok) {
        const err = await resp.json();
        alert('Encoding error: ' + (err.detail || 'Unknown error'));
        return;
      }

      const data = await resp.json();
      this.renderBreakdown(data);
    } catch (e) {
      console.error(e);
      alert('Failed to connect to ULPIN encoding service.');
    }
  }

  renderBreakdown(data) {
    let rowsHtml = '';
    const steps = data.steps || [];
    steps.forEach(step => {
      rowsHtml += `
        <tr>
          <td><strong>${step.segment}</strong></td>
          <td><code>${step.calculation}</code></td>
          <td><code>${step.raw_code}</code></td>
          <td><strong style="color: #00f2fe; font-family: monospace;">${step.clean_code}</strong></td>
        </tr>
      `;
    });

    const base2d = (data.ulpin || '832454DYJFAQY2').slice(0, 14);
    const floorNum = data.floor || 0;
    const suffix = floorNum < 0 ? `-B${Math.abs(floorNum).toString().padStart(2, '0')}` : `-F${floorNum.toString().padStart(2, '0')}`;
    const ulpin3d_18 = `${base2d}${suffix}`;

    if (this.breakdownContainer) {
      this.breakdownContainer.innerHTML = `
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px;">
          <div class="ulpin-badge" style="background: rgba(0, 242, 254, 0.1); border-color: #00f2fe;">
            <div class="ulpin-code" style="color: #00f2fe; font-size: 18px;">${base2d}</div>
            <div class="ulpin-desc">2D Cadastral Base ULPIN (14 Chars)</div>
          </div>
          <div class="ulpin-badge" style="background: rgba(168, 85, 247, 0.1); border-color: #a855f7;">
            <div class="ulpin-code" style="color: #a855f7; font-size: 18px;">${ulpin3d_18}</div>
            <div class="ulpin-desc">3D Volumetric ULPIN (18 Chars)</div>
          </div>
        </div>
        <table class="steps-table">
          <thead>
            <tr>
              <th>Component</th>
              <th>Arithmetic</th>
              <th>Raw Base</th>
              <th>Code</th>
            </tr>
          </thead>
          <tbody>
            ${rowsHtml}
          </tbody>
        </table>
      `;
    }
  }

  async performDecoding() {
    const inputElem = document.getElementById('input-ulpin-code');
    if (!inputElem) return;
    const ulpin = inputElem.value.trim();
    if (ulpin.length < 14) {
      alert('ULPIN must be at least 14 characters.');
      return;
    }

    try {
      const resp = await fetch('/api/ulpin/decode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ulpin: ulpin })
      });

      if (!resp.ok) {
        const err = await resp.json();
        alert('Decoding error: ' + (err.detail || 'Invalid ULPIN code'));
        return;
      }

      const data = await resp.json();
      if (this.decodeResultContainer) {
        this.decodeResultContainer.innerHTML = `
          <div class="card" style="margin-top: 14px; background: rgba(0, 242, 254, 0.05); border-color: #00f2fe;">
            <div class="card-title" style="color: #00f2fe;">Decoded Spatial Identity</div>
            <div style="font-family: monospace; font-size: 13px; line-height: 1.6; color: #e2e8f0; padding: 10px;">
              <div><strong>2D Base ULPIN (14 Chars):</strong> <span style="color: #00f2fe;">${data.ulpin_2d_base || data.ulpin}</span></div>
              <div><strong>3D Volumetric ULPIN (18 Chars):</strong> <span style="color: #a855f7;">${data.ulpin_3d_full || (data.ulpin + '-F00')}</span></div>
              <div><strong>Estimated Latitude:</strong> ${data.latitude}° N</div>
              <div><strong>Estimated Longitude:</strong> ${data.longitude}° E</div>
              <div><strong>Floor / Vertical Level:</strong> ${data.vertical_level_desc || data.floor}</div>
            </div>
          </div>
        `;
      }
    } catch (e) {
      console.error(e);
      alert('Failed to decode ULPIN.');
    }
  }
}
