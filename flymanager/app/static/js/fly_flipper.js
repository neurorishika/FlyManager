document.addEventListener("DOMContentLoaded", function() {
    // Connect to Socket.IO using the current origin instead of hardcoding port 5000
    const socket = io.connect(window.location.origin, {
        path: '/socket.io'
    });
    const scanSource = document.getElementById('scanSource');
    const startScanBtn = document.getElementById('startScanBtn');
    const stopScanBtn = document.getElementById('stopScanBtn');
    const portSelection = document.getElementById('serialPort');
    const serverScanControls = document.getElementById('server-scan-controls');
    const clientInputControls = document.getElementById('client-input-controls');
    const clientSerialControls = document.getElementById('client-serial-controls');
    const cameraScanControls = document.getElementById('camera-scan-controls');
    const scanWaiting = document.getElementById('scan-waiting');
    const browserScanInput = document.getElementById('browserScanInput');
    const lookupUidBtn = document.getElementById('lookupUidBtn');
    const connectClientSerialBtn = document.getElementById('connectClientSerialBtn');
    const disconnectClientSerialBtn = document.getElementById('disconnectClientSerialBtn');
    const clientSerialSupport = document.getElementById('clientSerialSupport');
    const cameraPreview = document.getElementById('cameraPreview');
    const startCameraScanBtn = document.getElementById('startCameraScanBtn');
    const stopCameraScanBtn = document.getElementById('stopCameraScanBtn');
    const scanStatusMessage = document.getElementById('scanStatusMessage');
    const scanSourceHint = document.getElementById('scanSourceHint');
    const scanModeTitle = document.getElementById('scanModeTitle');
    const scanModeSummary = document.getElementById('scanModeSummary');
    const scanModeRunsOn = document.getElementById('scanModeRunsOn');
    const scanModeBestFor = document.getElementById('scanModeBestFor');
    const scanModeRequirements = document.getElementById('scanModeRequirements');
    const scanModeBrowserScope = document.getElementById('scanModeBrowserScope');
    const webserialExplainer = document.getElementById('webserialExplainer');
    const compatServer = document.getElementById('compatServer');
    const compatKeyboard = document.getElementById('compatKeyboard');
    const compatCamera = document.getElementById('compatCamera');
    const compatWebSerial = document.getElementById('compatWebSerial');
    const serverSupportBadge = document.getElementById('serverSupportBadge');
    const keyboardSupportBadge = document.getElementById('keyboardSupportBadge');
    const cameraSupportBadge = document.getElementById('cameraSupportBadge');
    const webserialSupportBadge = document.getElementById('webserialSupportBadge');
    const stockDetails = document.getElementById('stock-details');
    let currentThreadId = null;
    let clientSerialScanner = null;
    let cameraScanner = null;
    let autonomousUidInput = null;

    const scanModeDefinitions = {
        'server': {
            title: 'Server Scanner',
            summary: 'Best when the QR reader is physically attached to the server host instead of the local workstation.',
            runsOn: 'Server host',
            bestFor: 'Shared bench setups with one scanner wired into the FlyManager machine.',
            requirements: 'A server-visible serial port and an operator using this page.',
            browserScope: 'Browser-independent',
            sourceHint: 'Server mode works regardless of the user browser because the scanner stays attached to the server machine.'
        },
        'client-input': {
            title: 'Browser Input / Keyboard Scanner',
            summary: 'Treat the scanner like a keyboard wedge. Focus the field and the UID submits automatically once all 10 hex characters arrive.',
            runsOn: 'Client browser tab',
            bestFor: 'Keyboard-wedge scanners and manual fallback entry on nearly any modern browser.',
            requirements: 'Focus on the UID field before scanning. Partial invalid input resets automatically.',
            browserScope: 'Works broadly',
            sourceHint: 'Keyboard-wedge mode works in standard browsers after the UID field is focused.'
        },
        'camera': {
            title: 'Camera Scanner',
            summary: 'Use the client device camera to decode QR codes directly in the browser when camera APIs and QR detection are available.',
            runsOn: 'Client browser tab',
            bestFor: 'Laptops or tablets without a dedicated scanner, especially for occasional bench work.',
            requirements: 'Secure session, camera permission, and browser QR detection support.',
            browserScope: 'Browser-dependent',
            sourceHint: 'Camera mode needs HTTPS or another secure context plus browser support for camera QR scanning.'
        },
        'client-serial': {
            title: 'Client Serial Scanner (WebSerial)',
            summary: 'Connect a serial scanner directly to the user workstation instead of the server, then scan locally through WebSerial.',
            runsOn: 'Client browser tab',
            bestFor: 'Dedicated workstation scanners connected to the operator computer instead of the server host.',
            requirements: 'Secure session, a compatible serial scanner, and Chrome or Edge on the client machine.',
            browserScope: 'Chrome or Edge only',
            sourceHint: 'WebSerial requires a secure Chrome or Edge session on the client machine.'
        }
    };

    function showElement(element) {
        element.classList.remove('is-hidden');
    }

    function hideElement(element) {
        element.classList.add('is-hidden');
    }

    function isVisible(element) {
        return !element.classList.contains('is-hidden');
    }

    function setScanStatus(message) {
        scanStatusMessage.textContent = message;
    }

    function setSupportBadge(element, text, stateClass) {
        if (!element) {
            return;
        }

        element.textContent = text;
        element.classList.remove('is-supported', 'is-limited', 'is-unavailable');
        if (stateClass) {
            element.classList.add(stateClass);
        }
    }

    function setCurrentCompatibilityCard(activeCard) {
        [compatServer, compatKeyboard, compatCamera, compatWebSerial].forEach(function(card) {
            if (!card) {
                return;
            }
            card.classList.toggle('is-current', card === activeCard);
        });
    }

    function updateCompatibilitySummary(source, webSerialSupported, cameraSupported) {
        setSupportBadge(serverSupportBadge, 'Available', 'is-supported');
        setSupportBadge(keyboardSupportBadge, 'Available', 'is-supported');

        setSupportBadge(
            cameraSupportBadge,
            cameraSupported ? 'Supported Here' : 'Not In This Browser',
            cameraSupported ? 'is-supported' : 'is-unavailable'
        );

        setSupportBadge(
            webserialSupportBadge,
            webSerialSupported ? 'Supported Here' : 'Chrome/Edge Only',
            webSerialSupported ? 'is-supported' : 'is-unavailable'
        );

        if (source === 'server') {
            setCurrentCompatibilityCard(compatServer);
        } else if (source === 'client-input') {
            setCurrentCompatibilityCard(compatKeyboard);
        } else if (source === 'camera') {
            setCurrentCompatibilityCard(compatCamera);
        } else if (source === 'client-serial') {
            setCurrentCompatibilityCard(compatWebSerial);
        }
    }

    function updateScanModeGuidance(source, webSerialSupported, cameraSupported) {
        const definition = scanModeDefinitions[source] || scanModeDefinitions.server;
        scanModeTitle.textContent = definition.title;
        scanModeSummary.textContent = definition.summary;
        scanModeRunsOn.textContent = definition.runsOn;
        scanModeBestFor.textContent = definition.bestFor;
        scanModeRequirements.textContent = definition.requirements;
        scanModeBrowserScope.textContent = definition.browserScope;
        scanSourceHint.textContent = definition.sourceHint;
        webserialExplainer.textContent = webSerialSupported
            ? 'WebSerial is supported by this browser session, so the client serial option is available in the Scan Source menu.'
            : 'This browser session does not support WebSerial. Use secure Chrome or Edge on the client machine to make the option usable.';

        if (source === 'camera' && !cameraSupported) {
            scanModeRequirements.textContent = 'This browser cannot use camera QR scanning in the current session. Use the server scanner, keyboard wedge, or Chrome/Edge WebSerial instead.';
        }

        if (source === 'client-serial' && !webSerialSupported) {
            scanModeRequirements.textContent = 'This browser cannot use WebSerial in the current session. Use secure Chrome or Edge on the client machine, or switch to another scan source.';
        }
    }

    // Function to get the current datetime in the local time zone
    function getLocalDateTime() {
        const now = new Date();
        const offset = now.getTimezoneOffset() * 60000; // getTimezoneOffset returns minutes, so convert to milliseconds
        const localTime = new Date(now - offset);
        return localTime.toISOString().slice(0, 16); // YYYY-MM-DDTHH:MM
    }

    function resetDisplayedStock() {
        document.getElementById('trayID').textContent = '';
        document.getElementById('trayPosition').textContent = '';
        document.getElementById('uniqueID').textContent = '';
        document.getElementById('seriesID').textContent = '';
        document.getElementById('replicateID').textContent = '';
        document.getElementById('name').textContent = '';
        document.getElementById('altReference').textContent = '';
        document.getElementById('genotype').textContent = '';
        document.getElementById('maleGenotype').textContent = '';
        document.getElementById('femaleGenotype').textContent = '';
        document.querySelector(`input[name="status"][value="Healthy"]`).checked = true;
        document.getElementById('flipTime').value = getLocalDateTime();
        document.getElementById('comment').value = '';
    }

    function renderScannedPayload(data) {
        document.getElementById('trayID').textContent = data.trayID || ' ';
        document.getElementById('trayPosition').textContent = data.trayPosition || ' ';
        document.getElementById('uniqueID').textContent = data.uniqueID || ' ';
        document.getElementById('seriesID').textContent = data.seriesID || ' ';
        document.getElementById('replicateID').textContent = data.replicateID || ' ';
        document.getElementById('name').textContent = data.name || ' ';
        document.getElementById('altReference').textContent = data.altReference || '';
        document.getElementById('genotype').textContent = data.genotype || ' ';
        document.getElementById('maleGenotype').textContent = data.maleGenotype || ' ';
        document.getElementById('femaleGenotype').textContent = data.femaleGenotype || ' ';
        if (data.status) {
            const statusOption = document.querySelector(`input[name="status"][value="${data.status}"]`);
            if (statusOption) {
                statusOption.checked = true;
            }
        }
        document.getElementById('flipTime').value = getLocalDateTime();
        showElement(stockDetails);
    }

    // Function to flip the stock
    function flipFliesFromView(options = {}) {
        const showSuccessAlert = options.showSuccessAlert !== false;
        if (isVisible(stockDetails)) {
            const status = document.querySelector('input[name="status"]:checked').value;
            const flipTime = document.getElementById('flipTime').value;
            const comment = document.getElementById('comment').value;
            const uniqueID = document.getElementById('uniqueID').textContent;

            return fetch(flipVialUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ status: status, flipTime: flipTime, comment: comment, uniqueID: uniqueID })
            })
            .then(response => response.json())
            .then(info => {
                resetDisplayedStock();
                hideElement(stockDetails);
                if (showSuccessAlert) {
                    alert(info.message || 'Stock flipped successfully!');
                }
            })
        }
        else {
            hideElement(stockDetails);
            return Promise.resolve()
        }
    }

    function processScanPayload(data) {
        if (isVisible(stockDetails)) {
            return flipFliesFromView({ showSuccessAlert: false }).then(() => {
                renderScannedPayload(data);
            });
        }

        renderScannedPayload(data);
        return Promise.resolve();
    }

    function stopLegacyScan(options = {}) {
        const shouldFlipCurrent = options.shouldFlipCurrent !== false;
        if (!currentThreadId) {
            hideElement(scanWaiting);
            showElement(startScanBtn);
            portSelection.disabled = false;
            return Promise.resolve();
        }

        const threadId = currentThreadId;
        currentThreadId = null;

        const completeStop = function() {
            hideElement(scanWaiting);
            showElement(startScanBtn);
            portSelection.disabled = false;
            return fetch(stopScanUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ thread_id: threadId })
            });
        };

        if (shouldFlipCurrent) {
            return flipFliesFromView({ showSuccessAlert: false }).then(completeStop);
        }

        return completeStop();
    }

    function ensureCameraScanner() {
        if (cameraScanner || !window.FlyManagerClientScanner) {
            return cameraScanner;
        }

        cameraScanner = window.FlyManagerClientScanner.createCameraScanner({
            onCode: function(uniqueID) {
                lookupUid(uniqueID);
            },
            onStatus: function(message) {
                setScanStatus(message);
            },
            onError: function(error) {
                const message = error && error.message ? error.message : 'Camera scanner error.';
                setScanStatus(message);
                alert(message);
                if (stopCameraScanBtn) {
                    hideElement(stopCameraScanBtn);
                }
                if (startCameraScanBtn) {
                    showElement(startCameraScanBtn);
                }
                if (cameraPreview) {
                    hideElement(cameraPreview);
                }
            }
        });

        return cameraScanner;
    }

    function stopCameraScan() {
        if (!cameraScanner || !cameraScanner.isActive()) {
            if (cameraPreview) {
                hideElement(cameraPreview);
            }
            if (stopCameraScanBtn) {
                hideElement(stopCameraScanBtn);
            }
            if (startCameraScanBtn) {
                showElement(startCameraScanBtn);
            }
            return Promise.resolve();
        }

        return cameraScanner.stop().finally(function() {
            if (cameraPreview) {
                hideElement(cameraPreview);
            }
            if (stopCameraScanBtn) {
                hideElement(stopCameraScanBtn);
            }
            if (startCameraScanBtn) {
                showElement(startCameraScanBtn);
            }
        });
    }

    function updateScanSourceView() {
        const source = scanSource.value;
        const webSerialSupported = window.FlyManagerClientScanner && window.FlyManagerClientScanner.supportsWebSerial();
        const cameraSupported = window.FlyManagerClientScanner && window.FlyManagerClientScanner.supportsCameraScanner();

        updateScanModeGuidance(source, webSerialSupported, cameraSupported);
        updateCompatibilitySummary(source, webSerialSupported, cameraSupported);

        serverScanControls.classList.toggle('is-hidden', source !== 'server');
        clientInputControls.classList.toggle('is-hidden', source !== 'client-input');
        if (clientSerialControls) {
            clientSerialControls.classList.toggle('is-hidden', source !== 'client-serial');
        }
        if (cameraScanControls) {
            cameraScanControls.classList.toggle('is-hidden', source !== 'camera');
        }
        scanWaiting.classList.toggle('is-hidden', source !== 'server' || !currentThreadId);

        if (source !== 'server' && currentThreadId) {
            stopLegacyScan({ shouldFlipCurrent: false });
        }

        if (source !== 'client-serial' && clientSerialScanner && clientSerialScanner.isActive()) {
            clientSerialScanner.stop();
            if (disconnectClientSerialBtn) {
                hideElement(disconnectClientSerialBtn);
            }
            if (connectClientSerialBtn) {
                showElement(connectClientSerialBtn);
            }
        }

        if (source !== 'camera' && cameraScanner && cameraScanner.isActive()) {
            stopCameraScan();
        }

        if (source === 'server') {
            setScanStatus('Using the server-attached serial scanner.');
        } else if (source === 'client-input') {
            setScanStatus('Focus the UID field and scan with a keyboard wedge. Valid 10-character UIDs submit automatically.');
            if (autonomousUidInput) {
                autonomousUidInput.focus();
            } else {
                browserScanInput.focus();
            }
        } else if (source === 'camera') {
            if (startCameraScanBtn) {
                startCameraScanBtn.disabled = !cameraSupported;
            }
            if (cameraSupported) {
                setScanStatus('Start the camera scanner and point the device camera at a QR code.');
            } else {
                setScanStatus('Camera scanning requires a secure browser session with camera access and QR decoding support.');
            }
        } else if (source === 'client-serial') {
            clientSerialSupport.value = webSerialSupported
                ? 'WebSerial is available in this secure Chrome or Edge session.'
                : 'This browser session cannot use WebSerial. Switch to secure Chrome or Edge on the client machine.';
            connectClientSerialBtn.disabled = !webSerialSupported;
            setScanStatus(webSerialSupported
                ? 'Connect a client-side serial scanner in Chrome or Edge, then scan locally.'
                : 'Client serial scanning is not available in this browser session.');
        }
    }

    function lookupUid(uniqueID) {
        const normalized = String(uniqueID || '').trim();
        if (!normalized) {
            return Promise.resolve();
        }

        setScanStatus(`Looking up ${normalized}...`);
        return fetch(lookupUidUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ uniqueID: normalized })
        })
        .then(async response => {
            const data = await response.json();
            if (!response.ok || !data.success) {
                throw new Error(data.message || 'UID not recognized.');
            }
            return data;
        })
        .then(data => {
            browserScanInput.value = '';
            return processScanPayload(data.payload).then(() => {
                setScanStatus(`Loaded ${data.itemType} ${normalized}.`);
            });
        })
        .catch(error => {
            browserScanInput.value = '';
            setScanStatus(error.message || 'Failed to process scanned UID.');
            alert(error.message || 'Failed to process scanned UID.');
        });
    }

    startScanBtn.addEventListener('click', function() {
        const selectedPort = portSelection.value;
        showElement(scanWaiting);
        hideElement(startScanBtn);
        portSelection.disabled = true;
        setScanStatus('Waiting for the server-attached scanner...');

        fetch(startScanUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ port_index: selectedPort })
        })
        .then(response => response.json())
        .then(data => {
            currentThreadId = data.thread_id;
            if (!data.success) {
                throw new Error(data.message || 'Failed to start scanning.');
            }
        })
        .catch(error => {
            currentThreadId = null;
            hideElement(scanWaiting);
            showElement(startScanBtn);
            portSelection.disabled = false;
            setScanStatus(error.message || 'Failed to start scanning.');
            alert(error.message || 'Failed to start scanning.');
        });
    });

    stopScanBtn.addEventListener('click', function() {
        stopLegacyScan();
    });

    window.addEventListener('beforeunload', function() {
        if (currentThreadId) {
            stopLegacyScan();
        }
        if (clientSerialScanner && clientSerialScanner.isActive()) {
            clientSerialScanner.stop();
        }
        if (cameraScanner && cameraScanner.isActive()) {
            cameraScanner.stop();
        }
    });

    socket.on('stock_scanned', function(data) {
        processScanPayload(data);
        setScanStatus(`Scanned ${data.uniqueID} from the server-attached scanner.`);
    });

    socket.on('cross_scanned', function(data) {
        processScanPayload(data);
        setScanStatus(`Scanned ${data.uniqueID} from the server-attached scanner.`);
    });

    socket.on('qr_not_recognized', function(data) {
        const uniqueID = data && data.uniqueID ? data.uniqueID : 'This UID';
        setScanStatus(`${uniqueID} was not recognized for the current user.`);
        alert(`${uniqueID} was not recognized for the current user.`);
    });

    socket.on('scan_error', function(data) {
        const message = data && data.message ? data.message : 'Scanner error.';
        setScanStatus(message);
        alert(message);
    });

    if (window.FlyManagerClientScanner && browserScanInput) {
        autonomousUidInput = window.FlyManagerClientScanner.createAutonomousUidInput({
            inputElement: browserScanInput,
            resetDelayMs: 10000,
            onMatch: function(uniqueID) {
                return lookupUid(uniqueID);
            }
        });
    }

    lookupUidBtn.addEventListener('click', function() {
        if (autonomousUidInput) {
            autonomousUidInput.focus();
            return;
        }
        browserScanInput.focus();
    });

    if (connectClientSerialBtn) {
        connectClientSerialBtn.addEventListener('click', function() {
        if (!window.FlyManagerClientScanner) {
            setScanStatus('Client serial support is unavailable on this page.');
            return;
        }

        if (!clientSerialScanner) {
            clientSerialScanner = window.FlyManagerClientScanner.createSerialScanner({
                baudRate: 9600,
                onCode: function(uniqueID) {
                    lookupUid(uniqueID);
                },
                onStatus: function(message) {
                    setScanStatus(message);
                },
                onError: function(error) {
                    const message = error && error.message ? error.message : 'Client serial scanner error.';
                    setScanStatus(message);
                    alert(message);
                    hideElement(disconnectClientSerialBtn);
                    showElement(connectClientSerialBtn);
                }
            });
        }

        clientSerialScanner.start()
            .then(function() {
                hideElement(connectClientSerialBtn);
                showElement(disconnectClientSerialBtn);
            })
            .catch(function(error) {
                const message = error && error.message ? error.message : 'Unable to connect the client serial scanner.';
                setScanStatus(message);
                alert(message);
            });
        });
    }

    if (disconnectClientSerialBtn) {
        disconnectClientSerialBtn.addEventListener('click', function() {
        if (!clientSerialScanner) {
            return;
        }

        clientSerialScanner.stop().finally(function() {
            hideElement(disconnectClientSerialBtn);
            showElement(connectClientSerialBtn);
        });
        });
    }

    if (startCameraScanBtn) {
        startCameraScanBtn.addEventListener('click', function() {
            const scanner = ensureCameraScanner();
            if (!scanner) {
                setScanStatus('Camera support is unavailable on this page.');
                return;
            }

            scanner.start(cameraPreview)
                .then(function() {
                    showElement(cameraPreview);
                    hideElement(startCameraScanBtn);
                    showElement(stopCameraScanBtn);
                })
                .catch(function(error) {
                    const message = error && error.message ? error.message : 'Unable to start the camera scanner.';
                    setScanStatus(message);
                    alert(message);
                });
        });
    }

    if (stopCameraScanBtn) {
        stopCameraScanBtn.addEventListener('click', function() {
            stopCameraScan();
        });
    }

    scanSource.addEventListener('change', updateScanSourceView);

    // Flip the stock when the flip button is clicked
    const flipBtn = document.getElementById('flipBtn');
    flipBtn.addEventListener('click', function() {
        flipFliesFromView({ showSuccessAlert: true }).then(function() {
            setScanStatus('Flip recorded.');
        })
    });

    // Cancel the flip when the cancel button is clicked
    const cancelFlipBtn = document.getElementById('cancelFlipBtn');
    cancelFlipBtn.addEventListener('click', function() {
        // Clear all the fields and hide the stock details section
        hideElement(stockDetails);
        resetDisplayedStock();
        document.querySelectorAll('input[name="status"]').forEach(input => input.checked = false);
        setScanStatus('Current vial cleared.');
    });

    resetDisplayedStock();
    updateScanSourceView();
});