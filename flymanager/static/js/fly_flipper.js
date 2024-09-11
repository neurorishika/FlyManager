document.addEventListener("DOMContentLoaded", function() {
    const socket = io.connect('http://127.0.0.1:5000');
    const startScanBtn = document.getElementById('startScanBtn');
    const stopScanBtn = document.getElementById('stopScanBtn');
    const portSelection = document.getElementById('serialPort');
    const scanWaiting = document.getElementById('scan-waiting');
    const stockDetails = document.getElementById('stock-details');
    let currentThreadId = null;

    // Function to get the current datetime in the local time zone
    function getLocalDateTime() {
        const now = new Date();
        const offset = now.getTimezoneOffset() * 60000; // getTimezoneOffset returns minutes, so convert to milliseconds
        const localTime = new Date(now - offset);
        return localTime.toISOString().slice(0, 16); // YYYY-MM-DDTHH:MM
    }

    // Function to flip the stock
    function flipFliesFromView() {
        if (stockDetails.style.display === 'block') {
            const status = document.querySelector('input[name="status"]:checked').value;
            const flipTime = document.getElementById('flipTime').value;
            const comment = document.getElementById('comment').value;
            const uniqueID = document.getElementById('uniqueID').textContent;

            return fetch('/flip_vial', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ status: status, flipTime: flipTime, comment: comment, uniqueID: uniqueID })
            })
            .then(response => response.json())
            .then(info => {
                document.getElementById('trayID').textContent = '';
                document.getElementById('trayPosition').textContent = '';
                document.getElementById('uniqueID').textContent = '';
                document.getElementById('seriesID').textContent = '';
                document.getElementById('replicateID').textContent = '';
                document.getElementById('name').textContent = '';
                document.getElementById('genotype').textContent = '';
                document.getElementById('maleGenotype').textContent = '';
                document.getElementById('femaleGenotype').textContent = '';
                document.querySelector(`input[name="status"][value="Healthy"]`).checked = true;
                document.getElementById('flipTime').value = getLocalDateTime();
                document.getElementById('comment').value = '';
                stockDetails.style.display = 'none';
                alert('Stock flipped successfully!');
            })
        }
        else {
            stockDetails.style.display = 'none';
            return Promise.resolve()
        }
    }

    startScanBtn.addEventListener('click', function() {
        const selectedPort = portSelection.value;
        scanWaiting.style.display = 'block';
        startScanBtn.style.display = 'none';
        portSelection.disabled = true;

        fetch('/start_scan', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ port_index: selectedPort })
        })
        .then(response => response.json())
        .then(data => {
            currentThreadId = data.thread_id;
        });
    });

    stopScanBtn.addEventListener('click', function() {
        if (currentThreadId) {
            // flip the stock
            flipFliesFromView().then(() => {
                scanWaiting.style.display = 'none';
                startScanBtn.style.display = 'block';
                portSelection.disabled = false;
                fetch('/stop_scan', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ thread_id: currentThreadId })
                });
            });
        }
    });

    window.addEventListener('beforeunload', function() {
        if (currentThreadId) {
            flipFliesFromView().then(() => {
                scanWaiting.style.display = 'none';
                startScanBtn.style.display = 'block';
                portSelection.disabled = false;
                fetch('/stop_scan', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ thread_id: currentThreadId })
                });
            });
        }
    });

    socket.on('stock_scanned', function(data) {
        // check if there is a fly being viewed
        if (stockDetails.style.display === 'block') {
            // flip the stock
            const status = document.querySelector('input[name="status"]:checked').value;
            const flipTime = document.getElementById('flipTime').value;
            const comment = document.getElementById('comment').value;
            const uniqueID = document.getElementById('uniqueID').textContent;

            // send the flip request and wait for the response, then display the stock details
            fetch('/flip_vial', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ status: status, flipTime: flipTime, comment: comment, uniqueID: uniqueID })
            })
            .then(response => response.json())
            .then(info => {
                // display the stock details
                document.getElementById('trayID').textContent = data.trayID;
                document.getElementById('trayPosition').textContent = data.trayPosition;
                document.getElementById('uniqueID').textContent = data.uniqueID;
                document.getElementById('seriesID').textContent = data.seriesID;
                document.getElementById('replicateID').textContent = data.replicateID;
                document.getElementById('name').textContent = data.name;
                document.getElementById('genotype').textContent = data.genotype;
                document.querySelector(`input[name="status"][value="${data.status}"]`).checked = true;
                document.getElementById('flipTime').value = getLocalDateTime();

                stockDetails.style.display = 'block';
            });
        }
        else {
            // display the stock details
            document.getElementById('trayID').textContent = data.trayID;
            document.getElementById('trayPosition').textContent = data.trayPosition;
            document.getElementById('uniqueID').textContent = data.uniqueID;
            document.getElementById('seriesID').textContent = data.seriesID;
            document.getElementById('replicateID').textContent = data.replicateID;
            document.getElementById('name').textContent = data.name;
            document.getElementById('genotype').textContent = data.genotype;
            document.querySelector(`input[name="status"][value="${data.status}"]`).checked = true;
            document.getElementById('flipTime').value = getLocalDateTime();

            stockDetails.style.display = 'block';
        }
    });

    socket.on('cross_scanned', function(data) {
        // check if there is a fly being viewed
        if (stockDetails.style.display === 'block') {
            // flip the stock
            const status = document.querySelector('input[name="status"]:checked').value;
            const flipTime = document.getElementById('flipTime').value;
            const comment = document.getElementById('comment').value;
            const uniqueID = document.getElementById('uniqueID').textContent;

            // send the flip request and wait for the response, then display the stock details
            fetch('/flip_vial', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ status: status, flipTime: flipTime, comment: comment, uniqueID: uniqueID })
            })
            .then(response => response.json())
            .then(info => {
                // display the stock details
                document.getElementById('trayID').textContent = data.trayID;
                document.getElementById('trayPosition').textContent = data.trayPosition;
                document.getElementById('uniqueID').textContent = data.uniqueID;
                document.getElementById('maleGenotype').textContent = data.maleGenotype;
                document.getElementById('femaleGenotype').textContent = data.femaleGenotype;
                document.getElementById('name').textContent = data.name;
                document.querySelector(`input[name="status"][value="${data.status}"]`).checked = true;
                document.getElementById('flipTime').value = getLocalDateTime();

                stockDetails.style.display = 'block';
            });
        }
        else {
            // display the stock details
            document.getElementById('trayID').textContent = data.trayID;
            document.getElementById('trayPosition').textContent = data.trayPosition;
            document.getElementById('uniqueID').textContent = data.uniqueID;
            document.getElementById('maleGenotype').textContent = data.maleGenotype;
            document.getElementById('femaleGenotype').textContent = data.femaleGenotype;
            document.getElementById('name').textContent = data.name;
            document.querySelector(`input[name="status"][value="${data.status}"]`).checked = true;
            document.getElementById('flipTime').value = getLocalDateTime();

            stockDetails.style.display = 'block';
        }
    });

    socket.on('qr_not_recognized', function() {
        alert('QR code not recognized. Please try again.');
    });

    // Flip the stock when the flip button is clicked
    const flipBtn = document.getElementById('flipBtn');
    flipBtn.addEventListener('click', function() {
        const status = document.querySelector('input[name="status"]:checked').value;
        const flipTime = document.getElementById('flipTime').value;
        const comment = document.getElementById('comment').value;
        const uniqueID = document.getElementById('uniqueID').textContent;
        
        fetch('/flip_vial', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ status: status, flipTime: flipTime, comment: comment, uniqueID: uniqueID })
        })
        .then(response => response.json())
        .then(data => {
            stockDetails.style.display = 'none';
            alert('Vial flipped successfully!');
        });
    });

    // Cancel the flip when the cancel button is clicked
    const cancelFlipBtn = document.getElementById('cancelFlipBtn');
    cancelFlipBtn.addEventListener('click', function() {
        // Clear all the fields and hide the stock details section
        stockDetails.style.display = 'none';
        document.getElementById('trayID').textContent = '';
        document.getElementById('trayPosition').textContent = '';
        document.getElementById('name').textContent = '';
        document.getElementById('altReference').textContent = '';
        document.getElementById('genotype').textContent = '';
        document.getElementById('uniqueID').textContent = '';
        document.getElementById('seriesID').textContent = '';
        document.getElementById('replicateID').textContent = '';
        document.getElementById('flipTime').value = '{{ now }}';
        document.getElementById('comment').value = '';
        document.querySelectorAll('input[name="status"]').forEach(input => input.checked = false);
    });
});