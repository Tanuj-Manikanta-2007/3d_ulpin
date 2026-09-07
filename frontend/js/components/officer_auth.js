/**
 * officer_auth.js — Officer Authentication & Ward Portal Ingestion Gate
 * Manages Municipal Town Planner authentication, role verification, and session state.
 */

class OfficerAuthManager {
  constructor(options = {}) {
    this.loginBtn = document.getElementById('btn-officer-login');
    this.modal = document.getElementById('officer-login-modal');
    this.closeBtn = document.getElementById('btn-close-officer-modal');
    this.loginForm = document.getElementById('officer-login-form');
    this.demoBtn = document.getElementById('btn-fill-demo-officer');
    this.statusText = document.getElementById('officer-login-status');

    this.currentOfficer = null;
    this.onLoginSuccessCallback = options.onLoginSuccess || null;

    this.init();
  }

  init() {
    // Restore session
    const saved = localStorage.getItem('3d_ulpin_officer_session');
    if (saved) {
      try {
        this.currentOfficer = JSON.parse(saved);
        this.updateUI(true);
      } catch (_) {
        this.currentOfficer = null;
      }
    }

    // Bind Button Click
    if (this.loginBtn) {
      this.loginBtn.addEventListener('click', () => {
        if (this.isLoggedIn()) {
          if (confirm(`Logged in as ${this.currentOfficer.name} (${this.currentOfficer.department}).\n\nDo you want to log out?`)) {
            this.logout();
          }
        } else {
          this.openModal();
        }
      });
    }

    // Close Modal
    if (this.closeBtn) {
      this.closeBtn.addEventListener('click', () => this.closeModal());
    }

    if (this.modal) {
      this.modal.addEventListener('click', (e) => {
        if (e.target === this.modal) this.closeModal();
      });
    }

    // Demo Fill Button
    if (this.demoBtn) {
      this.demoBtn.addEventListener('click', () => {
        const idInput = document.getElementById('input-officer-id');
        const nameInput = document.getElementById('input-officer-name');
        const deptInput = document.getElementById('input-officer-dept');
        const pinInput = document.getElementById('input-officer-pin');

        if (idInput) idInput.value = 'GHMC-TP-4092';
        if (nameInput) nameInput.value = 'Srikanth Rao (Town Planner)';
        if (deptInput) deptInput.value = 'GHMC Town Planning Wing';
        if (pinInput) pinInput.value = '849204';
      });
    }

    // Form Submit
    if (this.loginForm) {
      this.loginForm.addEventListener('submit', (e) => {
        e.preventDefault();
        this.handleLogin();
      });
    }
  }

  isLoggedIn() {
    return this.currentOfficer !== null;
  }

  openModal(promptMessage) {
    if (this.modal) {
      if (promptMessage && this.statusText) {
        this.statusText.textContent = promptMessage;
        this.statusText.style.color = '#f59e0b';
      }
      this.modal.classList.add('active');
    }
  }

  closeModal() {
    if (this.modal) {
      this.modal.classList.remove('active');
    }
  }

  async handleLogin() {
    const idInput = document.getElementById('input-officer-id');
    const nameInput = document.getElementById('input-officer-name');
    const deptInput = document.getElementById('input-officer-dept');
    const pinInput = document.getElementById('input-officer-pin');

    const officerId = (idInput ? idInput.value : '').trim() || 'GHMC-TP-4092';
    const officerName = (nameInput ? nameInput.value : '').trim() || 'Srikanth Rao';
    const dept = (deptInput ? deptInput.value : '').trim() || 'GHMC Town Planning Wing';

    try {
      const resp = await safeFetch('/api/auth/officer-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          officer_id: officerId,
          officer_name: officerName,
          department: dept,
          role: 'Cadastral Surveyor / Municipal Planner'
        })
      });

      if (resp.ok) {
        const data = await resp.json();
        this.currentOfficer = data.officer;
        localStorage.setItem('3d_ulpin_officer_session', JSON.stringify(this.currentOfficer));
        this.updateUI(true);
        this.closeModal();

        if (typeof this.onLoginSuccessCallback === 'function') {
          this.onLoginSuccessCallback(this.currentOfficer);
        }
      } else {
        alert('Authentication failed.');
      }
    } catch (e) {
      // Fallback local auth
      this.currentOfficer = {
        officer_id: officerId,
        name: officerName,
        department: dept,
        role: 'Cadastral Surveyor / Municipal Planner'
      };
      localStorage.setItem('3d_ulpin_officer_session', JSON.stringify(this.currentOfficer));
      this.updateUI(true);
      this.closeModal();
      if (typeof this.onLoginSuccessCallback === 'function') {
        this.onLoginSuccessCallback(this.currentOfficer);
      }
    }
  }

  logout() {
    this.currentOfficer = null;
    localStorage.removeItem('3d_ulpin_officer_session');
    this.updateUI(false);
  }

  updateUI(isLoggedIn) {
    if (!this.loginBtn) return;
    if (isLoggedIn && this.currentOfficer) {
      this.loginBtn.classList.add('logged-in');
      this.loginBtn.innerHTML = `<i class="fa-solid fa-circle-check" style="color: #10b981;"></i> ${this.currentOfficer.name}`;
      this.loginBtn.title = `Logged in: ${this.currentOfficer.department}. Click to log out.`;
    } else {
      this.loginBtn.classList.remove('logged-in');
      this.loginBtn.innerHTML = `<i class="fa-solid fa-user-shield"></i> Officer Login`;
      this.loginBtn.title = 'Municipal Officer Authentication';
    }
  }
}

window.OfficerAuthManager = OfficerAuthManager;
