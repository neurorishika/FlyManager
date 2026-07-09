(function () {
    'use strict';

    const POLL_INTERVAL_MS = 3000;
    const SEEN_STORAGE_KEY = 'flymanager_seen_job_states';

    function loadSeenStates() {
        try {
            return JSON.parse(sessionStorage.getItem(SEEN_STORAGE_KEY) || '{}');
        } catch (err) {
            return {};
        }
    }

    function saveSeenStates(states) {
        try {
            sessionStorage.setItem(SEEN_STORAGE_KEY, JSON.stringify(states));
        } catch (err) {
            // sessionStorage unavailable (private browsing, etc.) - degrade silently.
        }
    }

    function createBanner(container) {
        const banner = document.createElement('div');
        banner.id = 'job-status-banner';
        banner.className = 'job-status-banner';
        container.appendChild(banner);
        return banner;
    }

    function renderJobRow(job) {
        const row = document.createElement('div');
        row.className = 'job-status-row job-status-row--' + job.status;

        const label = document.createElement('span');
        label.className = 'job-status-row__label';
        label.textContent = job.label || job.key;
        row.appendChild(label);

        const status = document.createElement('span');
        status.className = 'job-status-row__status';
        if (job.status === 'queued') {
            status.textContent = 'Queued…';
        } else if (job.status === 'running') {
            status.textContent = job.progress && job.progress.message
                ? job.progress.message
                : 'Running…';
        } else if (job.status === 'succeeded') {
            status.textContent = (job.result && job.result.message) || 'Completed.';
        } else if (job.status === 'failed') {
            status.textContent = 'Failed: ' + (job.error || 'unknown error');
        }
        row.appendChild(status);

        if (job.status === 'succeeded' && job.metadata && job.metadata.route === 'download_data_route'
            && job.result && job.result.download_filename) {
            const link = document.createElement('a');
            link.href = '/data/download/' + encodeURIComponent(job.key) + '/file';
            link.textContent = 'Download';
            link.className = 'job-status-row__download';
            row.appendChild(link);
        }

        return row;
    }

    function poll(banner) {
        fetch('/jobs/status.json', { headers: { Accept: 'application/json' } })
            .then((response) => (response.ok ? response.json() : { jobs: [] }))
            .then((data) => {
                const jobs = data.jobs || [];
                const seen = loadSeenStates();
                const active = [];
                const justFinished = [];

                jobs.forEach((job) => {
                    const previousStatus = seen[job.key];
                    if (job.status === 'queued' || job.status === 'running') {
                        active.push(job);
                    } else if (
                        (job.status === 'succeeded' || job.status === 'failed')
                        && previousStatus
                        && previousStatus !== job.status
                    ) {
                        justFinished.push(job);
                    }
                    seen[job.key] = job.status;
                });
                saveSeenStates(seen);

                while (banner.firstChild) {
                    banner.removeChild(banner.firstChild);
                }
                const rowsToShow = active.concat(justFinished);
                if (rowsToShow.length === 0) {
                    banner.classList.remove('job-status-banner--visible');
                    return;
                }
                banner.classList.add('job-status-banner--visible');
                rowsToShow.forEach((job) => banner.appendChild(renderJobRow(job)));
            })
            .catch(() => { /* transient network hiccup - just try again next tick */ });
    }

    document.addEventListener('DOMContentLoaded', function () {
        const container = document.querySelector('.main-content');
        if (!container) {
            return;
        }
        const banner = createBanner(container);
        poll(banner);
        setInterval(() => poll(banner), POLL_INTERVAL_MS);
    });
})();
