(function(window) {
    const UID_LENGTH = 10;
    const UID_PATTERN = new RegExp(`^[0-9a-f]{${UID_LENGTH}}$`);
    const UID_PREFIX_PATTERN = new RegExp(`^[0-9a-f]{0,${UID_LENGTH}}$`);

    function normalizeUidValue(value) {
        return String(value || '').toLowerCase();
    }

    function isCompleteUid(value) {
        return UID_PATTERN.test(normalizeUidValue(value));
    }

    function supportsWebSerial() {
        return window.isSecureContext && typeof navigator !== 'undefined' && 'serial' in navigator;
    }

    function supportsCameraScanner() {
        return (
            window.isSecureContext &&
            typeof navigator !== 'undefined' &&
            navigator.mediaDevices &&
            typeof navigator.mediaDevices.getUserMedia === 'function' &&
            typeof window.BarcodeDetector !== 'undefined'
        );
    }

    function createSerialScanner(options = {}) {
        const baudRate = options.baudRate || 9600;
        const textDecoder = new TextDecoder();
        let activePort = null;
        let activeReader = null;
        let shouldRead = false;
        let readLoopPromise = null;
        let readBuffer = '';

        function emitStatus(message) {
            if (typeof options.onStatus === 'function') {
                options.onStatus(message);
            }
        }

        function emitCode(candidate) {
            const normalized = String(candidate || '').trim();
            if (normalized && typeof options.onCode === 'function') {
                options.onCode(normalized);
            }
        }

        function drainBuffer(flushRemaining = false) {
            let separatorIndex = readBuffer.search(/[\r\n]/);
            while (separatorIndex !== -1) {
                emitCode(readBuffer.slice(0, separatorIndex));
                readBuffer = readBuffer.slice(separatorIndex + 1);
                separatorIndex = readBuffer.search(/[\r\n]/);
            }

            if (flushRemaining && readBuffer.trim()) {
                emitCode(readBuffer);
                readBuffer = '';
            }
        }

        async function releaseReader() {
            if (!activeReader) {
                return;
            }

            try {
                await activeReader.cancel();
            } catch (error) {
                if (error && error.name !== 'InvalidStateError') {
                    throw error;
                }
            } finally {
                try {
                    activeReader.releaseLock();
                } catch (error) {
                    // Ignore lock release errors during shutdown.
                }
                activeReader = null;
            }
        }

        async function closePort() {
            if (!activePort) {
                return;
            }

            try {
                await activePort.close();
            } finally {
                activePort = null;
            }
        }

        async function readLoop() {
            try {
                while (shouldRead && activePort && activePort.readable) {
                    activeReader = activePort.readable.getReader();
                    try {
                        while (shouldRead) {
                            const { value, done } = await activeReader.read();
                            if (done) {
                                break;
                            }
                            if (value) {
                                readBuffer += textDecoder.decode(value, { stream: true });
                                drainBuffer(false);
                            }
                        }
                    } finally {
                        try {
                            activeReader.releaseLock();
                        } catch (error) {
                            // Ignore lock release errors during normal teardown.
                        }
                        activeReader = null;
                    }

                    break;
                }
            } catch (error) {
                if (shouldRead && typeof options.onError === 'function') {
                    options.onError(error);
                }
            } finally {
                drainBuffer(true);
                if (shouldRead) {
                    emitStatus('Client serial scanner disconnected.');
                }
                shouldRead = false;
                await closePort();
            }
        }

        async function start() {
            if (!supportsWebSerial()) {
                throw new Error('WebSerial requires a secure Chromium browser such as Chrome or Edge.');
            }
            if (shouldRead) {
                return;
            }

            readBuffer = '';
            activePort = await navigator.serial.requestPort();
            await activePort.open({ baudRate });
            shouldRead = true;
            emitStatus('Client serial scanner connected. Scan a QR code on this device.');
            readLoopPromise = readLoop();
            await Promise.resolve();
        }

        async function stop() {
            if (!shouldRead && !activePort) {
                return;
            }

            shouldRead = false;
            await releaseReader();
            await closePort();
            if (readLoopPromise) {
                try {
                    await readLoopPromise;
                } catch (error) {
                    // Ignore shutdown race conditions from pending reads.
                }
            }
            emitStatus('Client serial scanner disconnected.');
        }

        return {
            start,
            stop,
            isSupported: supportsWebSerial,
            isActive: function() {
                return shouldRead;
            },
        };
    }

    function createCameraScanner(options = {}) {
        let activeStream = null;
        let activeVideoElement = null;
        let activeDetector = null;
        let shouldScan = false;
        let animationFrameId = null;
        let lastCode = null;
        let lastScanAt = 0;

        function emitStatus(message) {
            if (typeof options.onStatus === 'function') {
                options.onStatus(message);
            }
        }

        function emitCode(candidate) {
            const normalized = String(candidate || '').trim();
            if (!normalized) {
                return;
            }

            const now = Date.now();
            if (normalized === lastCode && now - lastScanAt < 1500) {
                return;
            }

            lastCode = normalized;
            lastScanAt = now;
            if (typeof options.onCode === 'function') {
                options.onCode(normalized);
            }
        }

        async function scanFrame() {
            if (!shouldScan || !activeDetector || !activeVideoElement) {
                return;
            }

            try {
                if (activeVideoElement.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
                    const detections = await activeDetector.detect(activeVideoElement);
                    if (detections.length > 0) {
                        const firstDetection = detections.find((detection) => detection.rawValue);
                        if (firstDetection) {
                            emitCode(firstDetection.rawValue);
                        }
                    }
                }
            } catch (error) {
                if (shouldScan && typeof options.onError === 'function') {
                    options.onError(error);
                }
            } finally {
                if (shouldScan) {
                    animationFrameId = window.requestAnimationFrame(scanFrame);
                }
            }
        }

        async function stopTracks() {
            if (!activeStream) {
                return;
            }

            activeStream.getTracks().forEach((track) => track.stop());
            activeStream = null;
        }

        async function start(videoElement) {
            if (!supportsCameraScanner()) {
                throw new Error('Camera QR scanning requires a secure browser session with BarcodeDetector support.');
            }
            if (!videoElement) {
                throw new Error('A video element is required to start camera scanning.');
            }
            if (shouldScan) {
                return;
            }

            activeDetector = new window.BarcodeDetector({ formats: ['qr_code'] });
            activeStream = await navigator.mediaDevices.getUserMedia({
                audio: false,
                video: {
                    facingMode: { ideal: 'environment' },
                },
            });

            activeVideoElement = videoElement;
            activeVideoElement.srcObject = activeStream;
            activeVideoElement.setAttribute('playsinline', 'true');
            await activeVideoElement.play();

            shouldScan = true;
            lastCode = null;
            lastScanAt = 0;
            emitStatus('Camera scanner connected. Point the camera at a QR code.');
            animationFrameId = window.requestAnimationFrame(scanFrame);
        }

        async function stop() {
            shouldScan = false;
            if (animationFrameId) {
                window.cancelAnimationFrame(animationFrameId);
                animationFrameId = null;
            }
            await stopTracks();
            if (activeVideoElement) {
                activeVideoElement.pause();
                activeVideoElement.srcObject = null;
            }
            activeVideoElement = null;
            activeDetector = null;
            emitStatus('Camera scanner disconnected.');
        }

        return {
            start,
            stop,
            isSupported: supportsCameraScanner,
            isActive: function() {
                return shouldScan;
            },
        };
    }

    function createAutonomousUidInput(options = {}) {
        const inputElement = options.inputElement;
        const resetDelayMs = options.resetDelayMs || 10000;
        let resetTimerId = null;
        let pendingMatch = null;
        let lastMatchedValue = null;

        if (!inputElement) {
            throw new Error('An input element is required for autonomous UID capture.');
        }

        function cancelResetTimer() {
            if (resetTimerId) {
                window.clearTimeout(resetTimerId);
                resetTimerId = null;
            }
        }

        function notifyReset() {
            if (typeof options.onReset === 'function') {
                options.onReset();
            }
        }

        function reset() {
            cancelResetTimer();
            pendingMatch = null;
            lastMatchedValue = null;
            if (inputElement.value) {
                inputElement.value = '';
                notifyReset();
            }
        }

        function scheduleResetTimer() {
            cancelResetTimer();
            if (!inputElement.value) {
                return;
            }

            resetTimerId = window.setTimeout(function() {
                reset();
            }, resetDelayMs);
        }

        function handleValue(rawValue) {
            const normalizedValue = normalizeUidValue(rawValue);
            if (inputElement.value !== normalizedValue) {
                inputElement.value = normalizedValue;
            }

            if (!normalizedValue) {
                cancelResetTimer();
                pendingMatch = null;
                lastMatchedValue = null;
                return;
            }

            if (!UID_PREFIX_PATTERN.test(normalizedValue)) {
                reset();
                return;
            }

            scheduleResetTimer();

            if (!UID_PATTERN.test(normalizedValue)) {
                pendingMatch = null;
                lastMatchedValue = null;
                return;
            }

            cancelResetTimer();
            if (pendingMatch === normalizedValue || lastMatchedValue === normalizedValue) {
                return;
            }

            pendingMatch = normalizedValue;
            lastMatchedValue = normalizedValue;
            Promise.resolve(typeof options.onMatch === 'function' ? options.onMatch(normalizedValue) : undefined)
                .finally(function() {
                    pendingMatch = null;
                });
        }

        inputElement.addEventListener('input', function() {
            handleValue(inputElement.value);
        });

        inputElement.addEventListener('blur', function() {
            if (inputElement.value && !UID_PATTERN.test(normalizeUidValue(inputElement.value))) {
                reset();
                return;
            }
            cancelResetTimer();
        });

        return {
            focus: function() {
                inputElement.focus();
                if (typeof inputElement.select === 'function') {
                    inputElement.select();
                }
            },
            reset,
            handleValue,
            isCompleteUid,
        };
    }

    window.FlyManagerClientScanner = {
        createAutonomousUidInput,
        isCompleteUid,
        normalizeUidValue,
        supportsCameraScanner,
        supportsWebSerial,
        createCameraScanner,
        createSerialScanner,
    };
})(window);