# D. manager

<!-- markdownlint-disable MD033 -->

![D. manager banner](assets/branding/banner.png){ .hero-banner }

<div class="hero-copy">
<p class="hero-lead">A clean, lab-ready workspace for Drosophila stock operations.</p>
<p>D. manager brings stocks, crosses, trays, flip schedules, labels, and operator workflows into a single web application backed by Flask and MongoDB. The public product identity is D. manager; the repository and package name remain FlyManager.</p>
</div>

<div class="cta-row">
  <a class="md-button md-button--primary" href="https://github.com/neurorishika/FlyManager">View Repository</a>
  <a class="md-button" href="https://github.com/neurorishika/FlyManager/blob/main/DEPLOYMENT.md">Deployment Guide</a>
</div>

## At A Glance

<div class="feature-grid">
  <div class="feature-card">
    <h3>Core Lab Workflows</h3>
    <p>Track stocks, crosses, trays, and flip state from one interface instead of spread across paper labels and ad hoc spreadsheets.</p>
  </div>
  <div class="feature-card">
    <h3>Operationally Ready</h3>
    <p>Ship with Docker Compose, MongoDB backups, state backups, readiness checks, and a production HTTPS overlay.</p>
  </div>
  <div class="feature-card">
    <h3>Scanner And Label Support</h3>
    <p>Generate printable labels, streamline flips, and reduce manual handling during high-volume maintenance work.</p>
  </div>
  <div class="feature-card">
    <h3>Expandable Documentation</h3>
    <p>This site starts with a branded landing page and links into the operator runbooks already maintained in the repository.</p>
  </div>
</div>

## Primary Paths

<div class="link-grid">
  <a class="link-card" href="https://github.com/neurorishika/FlyManager/blob/main/DEPLOYMENT.md">
    <strong>Deployment</strong>
    <span>Base install, Docker startup, and production HTTPS setup.</span>
  </a>
  <a class="link-card" href="https://github.com/neurorishika/FlyManager/blob/main/PLATFORM_DEPLOYMENT.md">
    <strong>Platform Runbooks</strong>
    <span>Environment-specific deployment notes for Synology, Ubuntu, AWS, and GCP.</span>
  </a>
  <a class="link-card" href="https://github.com/neurorishika/FlyManager/blob/main/BACKUP_AND_RECOVERY.md">
    <strong>Backup And Recovery</strong>
    <span>MongoDB archives, state snapshots, and restore drill guidance.</span>
  </a>
  <a class="link-card" href="https://github.com/neurorishika/FlyManager#quick-start">
    <strong>Quick Start</strong>
    <span>Fastest route to a working local deployment from the repository root.</span>
  </a>
</div>

## Technical Profile

- Flask application with MongoDB persistence
- Docker Compose development and production deployment paths
- Caddy-based HTTPS overlay for public hosting
- GitHub Pages documentation with MkDocs Material

## Brand System

The circular fly mark is used for compact UI surfaces and documentation identity, while the horizontal banner anchors the README and docs landing page. This keeps the lab-facing product consistent without changing the technical repository naming.

<!-- markdownlint-enable MD033 -->