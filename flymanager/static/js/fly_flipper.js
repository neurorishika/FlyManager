document.addEventListener("DOMContentLoaded", function() {
    const startScanBtn = document.getElementById('startScanBtn');
    const portSelection = document.getElementById('port-selection');
    const scanWaiting = document.getElementById('scan-waiting');
    const stockDetails = document.getElementById('stock-details');

    startScanBtn.addEventListener('click', function() {
        const selectedPort = document.getElementById('serialPort').value;
        portSelection.style.display = 'none';
        scanWaiting.style.display = 'block';

        // Make a POST request to start scanning
        fetch(`/start_scan`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ port_index: selectedPort })
        })
        .then(response => response.json())
        .then(data => {
            scanWaiting.style.display = 'none';
            if (data.success) {
                // Display stock details
                document.getElementById('seriesID').textContent = data.stock.seriesID;
                document.getElementById('replicateID').textContent = data.stock.replicateID;
                document.getElementById('name').textContent = data.stock.name;
                document.getElementById('genotype').textContent = data.stock.genotype;
                stockDetails.style.display = 'block';
            } else {
                alert('QR code not recognized. Please try again.');
                portSelection.style.display = 'block';
            }
        })
        .catch(error => {
            alert('Failed to connect to the serial port. Please try again.');
            portSelection.style.display = 'block';
            scanWaiting.style.display = 'none';
        });
    });

    const flipBtn = document.getElementById('flipBtn');
    flipBtn.addEventListener('click', function() {
        const status = document.querySelector('input[name="status"]:checked').value;
        const flipTime = document.getElementById('flipTime').value;
        const comment = document.getElementById('comment').value;

        fetch(`/flip`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ status: status, flipTime: flipTime, comment: comment })
        })
        .then(response => response.json())
        .then(data => {
            alert(data.message);
            stockDetails.style.display = 'none';
            portSelection.style.display = 'block';
        })
        .catch(error => {
            alert('Failed to flip stock. Please try again.');
        });
    });
});
