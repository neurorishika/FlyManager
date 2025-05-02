let cart = JSON.parse(localStorage.getItem('cart')) || [];
let currentView = localStorage.getItem('stockViewMode') || 'card';
let currentSort = { column: '', direction: 'asc' };

function saveCart() {
    localStorage.setItem('cart', JSON.stringify(cart));
}

// Save current view preference
function saveViewPreference() {
    localStorage.setItem('stockViewMode', currentView);
}

function updateCart() {
    let cartItems = document.getElementById('cartItems');
    cartItems.innerHTML = '';
    if (cart.length === 0) {
        cartItems.innerHTML = '<p>Your cart is empty.</p>';
    } else {
        cart.forEach(function(item, index) {
            cartItems.innerHTML += `<div class="cart-item">
                <span>${item.identifier} - ${item.name}</span>
                <input type="number" class="form-control quantity-input" value="${item.quantity}" min="1" style="width: 60px; display: inline-block; margin: 0 10px;" onchange="updateQuantity(${index}, this.value)">
                <button class="btn btn-danger btn-sm" onclick="removeFromCart(${index})">Remove</button>
            </div>`;
        });
    }
    
    // Update cart count
    document.getElementById('cartCount').textContent = cart.length;
    saveCart();
}

function updateQuantity(index, quantity) {
    cart[index].quantity = quantity;
    saveCart();
}

function removeFromCart(index) {
    cart.splice(index, 1);
    updateCart();
}

function emptyCart() {
    cart = [];
    updateCart();
}

// Function to get the current datetime in the local time zone
function getLocalDateTime() {
    const now = new Date();
    const offset = now.getTimezoneOffset() * 60000; // getTimezoneOffset returns minutes, so convert to milliseconds
    const localTime = new Date(now - offset);
    return localTime.toISOString().slice(0, 16); // YYYY-MM-DDTHH:MM
}

// Toggle between card and table views
function toggleView(viewMode) {
    const cardView = document.getElementById('cardView');
    const tableView = document.getElementById('tableView');
    const cardViewBtn = document.getElementById('cardViewBtn');
    const tableViewBtn = document.getElementById('tableViewBtn');
    
    if (viewMode === 'card') {
        cardView.style.display = 'grid';
        tableView.style.display = 'none';
        cardViewBtn.classList.add('active');
        tableViewBtn.classList.remove('active');
        currentView = 'card';
    } else {
        cardView.style.display = 'none';
        tableView.style.display = 'block';
        cardViewBtn.classList.remove('active');
        tableViewBtn.classList.add('active');
        currentView = 'table';
    }
    
    saveViewPreference();
}

// Helper function for comparing tray values
function compareTrayValues(a, b) {
    // Handle empty/null/undefined values
    const emptyA = !a || a.trim() === '';
    const emptyB = !b || b.trim() === '';
    
    // If both are empty, they're equal
    if (emptyA && emptyB) return 0;
    
    // Empty values should sort to the end
    if (emptyA) return 1;  // a is empty, move it to end
    if (emptyB) return -1; // b is empty, move it to end
    
    // Now we know both a and b are non-empty strings
    const [aPrefix, aSuffix] = (a || '').split('-');
    const [bPrefix, bSuffix] = (b || '').split('-');
    
    // Compare prefixes first (case-insensitive)
    const prefixComparison = aPrefix.toLowerCase().localeCompare(bPrefix.toLowerCase());
    if (prefixComparison !== 0) return prefixComparison;
    
    // If we get here, prefixes are equal, compare numeric suffixes
    const aSuffixNum = aSuffix ? parseInt(aSuffix, 10) : NaN;
    const bSuffixNum = bSuffix ? parseInt(bSuffix, 10) : NaN;
    
    // Handle cases where one or both suffixes are not valid numbers
    if (isNaN(aSuffixNum) && isNaN(bSuffixNum)) return 0; // both invalid, consider equal
    if (isNaN(aSuffixNum)) return 1; // a's suffix invalid, move to end
    if (isNaN(bSuffixNum)) return -1; // b's suffix invalid, move to end
    
    // Both suffixes are valid numbers, compare them
    return aSuffixNum - bSuffixNum;
}

// Sort table by column
function sortTable(column) {
    const table = document.querySelector('#tableView table');
    const headers = table.querySelectorAll('th.sortable');
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    
    // Update sort direction
    if (currentSort.column === column) {
        currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        currentSort.column = column;
        currentSort.direction = 'asc';
    }
    
    // Update header classes
    headers.forEach(header => {
        header.classList.remove('asc', 'desc');
        if (header.dataset.sort === column) {
            header.classList.add(currentSort.direction);
        }
    });
    
    // Get column index (+2 to account for checkbox and expand columns)
    const columnIndex = Array.from(headers).findIndex(header => header.dataset.sort === column) + 2;
    
    // Sort rows
    rows.sort((a, b) => {
        let aValue = a.querySelector(`td:nth-child(${columnIndex})`).textContent.trim();
        let bValue = b.querySelector(`td:nth-child(${columnIndex})`).textContent.trim();

        let comparison;
        // Special case for tray column
        if (column === 'tray') {
            comparison = compareTrayValues(aValue, bValue);
        } else if (column === 'flipin' || column === 'eclose') {
            // Special sorting priority for time-based columns
            const timeValues = {
                'Overdue': 0,
                'Today': 1,
                'Tomorrow': 2,
                'days': 3
            };
            
            // Extract numeric values from "X days" format
            const aNumDays = aValue.match(/(\d+) days?/);
            const bNumDays = bValue.match(/(\d+) days?/);
            
            // Determine priorities based on time phrases
            let aPriority = 999;
            let bPriority = 999;
            
            // Check for priority keywords
            for (const [key, value] of Object.entries(timeValues)) {
                if (aValue.includes(key)) {
                    if (key === 'days' && aNumDays) {
                        aPriority = value + parseInt(aNumDays[1], 10);
                    } else {
                        aPriority = value;
                    }
                }
                if (bValue.includes(key)) {
                    if (key === 'days' && bNumDays) {
                        bPriority = value + parseInt(bNumDays[1], 10);
                    } else {
                        bPriority = value;
                    }
                }
            }
            
            // Compare based on priority
            if (aPriority !== bPriority) {
                comparison = aPriority - bPriority;
            } else if (aNumDays && bNumDays) {
                // If same priority type but with numbers, sort by the number
                comparison = parseInt(aNumDays[1], 10) - parseInt(bNumDays[1], 10);
            } else {
                comparison = aValue.localeCompare(bValue, undefined, { numeric: true });
            }
        } else {
            // Regular string comparison for other cases
            comparison = aValue.localeCompare(bValue, undefined, { numeric: true });
        }
        
        // Apply sort direction
        return currentSort.direction === 'asc' ? comparison : -comparison;
    });
    
    // Reorder rows
    const tbody = table.querySelector('tbody');
    rows.forEach(row => tbody.appendChild(row));
}

// Toggle row checkbox in table view
function toggleTableCheckbox(checkboxId, event) {
    event.stopPropagation();
    const checkbox = document.getElementById(checkboxId);
    const row = checkbox.closest('tr');
    
    if (checkbox.checked) {
        row.classList.add('selected');
    } else {
        row.classList.remove('selected');
    }
    
    // Sync with card view if same item
    const index = checkboxId.replace('stockTable', '');
    const cardCheckbox = document.getElementById('stock' + index);
    if (cardCheckbox) {
        cardCheckbox.checked = checkbox.checked;
        const card = document.getElementById('item-' + index);
        if (checkbox.checked) {
            card.classList.add('checked');
        } else {
            card.classList.remove('checked');
        }
    }
}

// Toggle row selection when clicking on row
function toggleRowSelection(row, event) {
    if (event.target.tagName !== 'INPUT' && event.target.tagName !== 'BUTTON' && !event.target.closest('button')) {
        const checkbox = row.querySelector('input[type="checkbox"]');
        checkbox.checked = !checkbox.checked;
        if (checkbox.checked) {
            row.classList.add('selected');
        } else {
            row.classList.remove('selected');
        }
        
        // Sync with card view
        const index = checkbox.id.replace('stockTable', '');
        const cardCheckbox = document.getElementById('stock' + index);
        if (cardCheckbox) {
            cardCheckbox.checked = checkbox.checked;
            const card = document.getElementById('item-' + index);
            if (checkbox.checked) {
                card.classList.add('checked');
            } else {
                card.classList.remove('checked');
            }
        }
    }
}

// Toggle details in table view
function toggleTableDetails(index) {
    const details = document.getElementById('tableDetails-' + index);
    if (details.style.display === 'none') {
        details.style.display = 'block';
    } else {
        details.style.display = 'none';
    }
}

document.getElementById('selectAllBtn').addEventListener('click', function() {
    if (currentView === 'card') {
        document.querySelectorAll('.stock-item input[type="checkbox"]').forEach(function(checkbox) {
            checkbox.checked = true;
            const card = document.getElementById('item-' + checkbox.id.replace('stock', ''));
            card.classList.add('checked');
        });
    } else {
        document.getElementById('selectAllTable').checked = true;
        document.querySelectorAll('#tableView tbody input[type="checkbox"]').forEach(function(checkbox) {
            checkbox.checked = true;
            checkbox.closest('tr').classList.add('selected');
            
            // Sync with card view
            const index = checkbox.id.replace('stockTable', '');
            const cardCheckbox = document.getElementById('stock' + index);
            if (cardCheckbox) {
                cardCheckbox.checked = true;
                document.getElementById('item-' + index).classList.add('checked');
            }
        });
    }
});

document.getElementById('deselectAllBtn').addEventListener('click', function() {
    if (currentView === 'card') {
        document.querySelectorAll('.stock-item input[type="checkbox"]').forEach(function(checkbox) {
            checkbox.checked = false;
            const card = document.getElementById('item-' + checkbox.id.replace('stock', ''));
            card.classList.remove('checked');
        });
    } else {
        document.getElementById('selectAllTable').checked = false;
        document.querySelectorAll('#tableView tbody input[type="checkbox"]').forEach(function(checkbox) {
            checkbox.checked = false;
            checkbox.closest('tr').classList.remove('selected');
            
            // Sync with card view
            const index = checkbox.id.replace('stockTable', '');
            const cardCheckbox = document.getElementById('stock' + index);
            if (cardCheckbox) {
                cardCheckbox.checked = false;
                document.getElementById('item-' + index).classList.remove('checked');
            }
        });
    }
});

document.querySelectorAll('.stock-item input[type="checkbox"]').forEach(function(checkbox) {
    checkbox.addEventListener('change', function() {
        if (checkbox.checked) {
            document.getElementById('item-' + checkbox.id.replace('stock', '')).classList.add('selected');
        } else {
            document.getElementById('item-' + checkbox.id.replace('stock', '')).classList.remove('selected');
        }
        
        // Sync with table view
        const index = checkbox.id.replace('stock', '');
        const tableCheckbox = document.getElementById('stockTable' + index);
        if (tableCheckbox) {
            tableCheckbox.checked = checkbox.checked;
            if (checkbox.checked) {
                tableCheckbox.closest('tr').classList.add('selected');
            } else {
                tableCheckbox.closest('tr').classList.remove('selected');
            }
        }
    });
});

document.querySelector('button[name="clear_filters"]').addEventListener('click', function(event) {
    if (!confirm('Are you sure you want to clear all filters?')) {
        event.preventDefault();
    }
});

document.getElementById('addToCartBtn').addEventListener('click', function() {
    let checkedItems;
    
    if (currentView === 'card') {
        checkedItems = document.querySelectorAll('.stock-item input[type="checkbox"]:checked');
        checkedItems.forEach(function(checkbox) {
            let index = checkbox.id.replace('stock', '');
            let stockItem = document.getElementById('item-' + index);
            let identifier = stockItem.querySelector('h5').textContent.split('|')[0].trim();
            let name = stockItem.querySelector('h5').textContent.split('|')[1].trim();
            let uid = stockItem.querySelector('p i').textContent.trim();
            let item = {
                id: index,
                quantity: 1,
                identifier: identifier,
                name: name,
                uid: uid
            };

            let existingItem = cart.find(cartItem => cartItem.uid === item.uid);
            if (existingItem) {
                existingItem.quantity++;
            } else {
                cart.push(item);
            }
        });
    } else {
        checkedItems = document.querySelectorAll('#tableView tbody input[type="checkbox"]:checked');
        checkedItems.forEach(function(checkbox) {
            let row = checkbox.closest('tr');
            let index = checkbox.id.replace('stockTable', '');
            let identifier = row.querySelector('.tray-cell').textContent.trim();
            let seriesId = row.querySelector('td:nth-child(3)').textContent.trim();
            let name = row.querySelector('td:nth-child(4)').textContent.trim();
            
            // Get the UID - first check if the details panel is already open
            let uid = '';
            let detailsPanel = document.getElementById('tableDetails-' + index);
            
            if (detailsPanel && detailsPanel.style.display !== 'none') {
                // Try to find UID in the displayed details panel
                let uidElements = detailsPanel.querySelectorAll('p strong');
                for (let el of uidElements) {
                    if (el.textContent === 'Unique ID:') {
                        uid = el.nextSibling.textContent.trim();
                        break;
                    }
                }
            }
            
            // If UID not found in details panel, get it from the card view
            if (!uid) {
                let stockItem = document.getElementById('item-' + index);
                if (stockItem) {
                    uid = stockItem.querySelector('p i').textContent.trim();
                }
            }
            
            let item = {
                id: index,
                quantity: 1,
                identifier: identifier || (seriesId + " / No tray"),
                name: name,
                uid: uid
            };

            let existingItem = cart.find(cartItem => cartItem.uid === item.uid);
            if (existingItem) {
                existingItem.quantity++;
            } else {
                cart.push(item);
            }
        });
    }

    updateCart();
    clearSelection();
});

function clearSelection() {
    document.querySelectorAll('.stock-item input[type="checkbox"]').forEach(function(checkbox) {
        checkbox.checked = false;
        const card = document.getElementById('item-' + checkbox.id.replace('stock', ''));
        card.classList.remove('selected');
        card.classList.remove('checked');
    });
    
    document.querySelectorAll('#tableView tbody input[type="checkbox"]').forEach(function(checkbox) {
        checkbox.checked = false;
        checkbox.closest('tr').classList.remove('selected');
    });
    
    document.getElementById('selectAllTable').checked = false;
}

document.getElementById('generateLabelsBtn').addEventListener('click', function() {
    if (cart.length > 0) {
        let selectedUids = cart.map(item => item.uid).join(',');
        let quantities = cart.map(item => item.quantity).join(',');
        let blankSpaces = prompt('How many blank spaces should be left?');
        if (blankSpaces !== null) {
            let form = document.getElementById('generateLabelsForm');
            document.getElementById('selectedUids').value = selectedUids;
            document.getElementById('blankSpaces').value = blankSpaces;
            document.getElementById('quantities').value = quantities;
            form.submit();
        }
    } else {
        alert('Your cart is empty.');
    }
    emptyCart();
});

// Bulk Flip Button - Open modal with confirmation
document.getElementById('bulkFlipBtn').addEventListener('click', function() {
    if (cart.length === 0) {
        alert('Your cart is empty. Please add items to flip.');
        return;
    }
    
    // Set up the modal with current information
    document.getElementById('bulkFlipCount').textContent = cart.length;
    document.getElementById('bulkFlipTime').value = getLocalDateTime();
    
    // Show the modal using Bootstrap 4 syntax
    $('#bulkFlipModal').modal('show');
});

// Bulk Flip Confirmation Button
document.getElementById('confirmBulkFlipBtn').addEventListener('click', function() {
    if (cart.length === 0) {
        alert('Your cart is empty.');
        return;
    }
    
    const flipTime = document.getElementById('bulkFlipTime').value;
    const comment = document.getElementById('bulkFlipComment').value;
    const status = document.getElementById('bulkFlipStatus').value;
    
    if (!flipTime) {
        alert('Please select a flip time.');
        return;
    }
    
    const uniqueIDs = cart.map(item => item.uid);
    
    fetch(bulkFlipUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            uniqueIDs: uniqueIDs,
            flipTime: flipTime,
            comment: comment,
            status: status || undefined
        })
    })
    .then(response => response.json())
    .then(data => {
        // Close the modal using Bootstrap 4 syntax
        $('#bulkFlipModal').modal('hide');
        
        // Show result
        alert(data.message);
        
        // Clear cart if successful
        if (data.results && data.results.success && data.results.success.length > 0) {
            emptyCart();
        }
    })
    .catch(error => {
        console.error('Error during bulk flip:', error);
        alert('Error occurred during bulk flip operation.');
    });
});

// Bulk Status Change dropdown items
document.querySelectorAll('.bulk-status-item').forEach(function(item) {
    item.addEventListener('click', function(event) {
        event.preventDefault();
        
        if (cart.length === 0) {
            alert('Your cart is empty. Please add items to change their status.');
            return;
        }
        
        const status = this.getAttribute('data-status');
        const uniqueIDs = cart.map(item => item.uid);
        
        if (confirm(`Are you sure you want to change the status of ${cart.length} items to "${status}"?`)) {
            fetch(bulkStatusUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    uniqueIDs: uniqueIDs,
                    status: status
                })
            })
            .then(response => response.json())
            .then(data => {
                alert(data.message);
                
                // Clear cart if successful
                if (data.results && data.results.success && data.results.success.length > 0) {
                    emptyCart();
                }
            })
            .catch(error => {
                console.error('Error during bulk status change:', error);
                alert('Error occurred during bulk status change operation.');
            });
        }
    });
});

// Bulk Remove from Tray Button
document.getElementById('removeFromTrayBtn').addEventListener('click', function() {
    if (cart.length === 0) {
        alert('Your cart is empty. Please add items to remove from trays.');
        return;
    }
    
    const uniqueIDs = cart.map(item => item.uid);
    
    if (confirm(`Are you sure you want to remove ${cart.length} items from their trays?`)) {
        fetch(bulkRemoveFromTrayUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                item_type: 'stock',
                uniqueIDs: uniqueIDs
            })
        })
        .then(response => response.json())
        .then(data => {
            alert(data.message);
            
            // Clear cart if successful
            if (data.results && data.results.success && data.results.success.length > 0) {
                emptyCart();
            }
            
            // Refresh page to show updated trays
            window.location.reload();
        })
        .catch(error => {
            console.error('Error during bulk remove from tray:', error);
            alert('Error occurred during bulk remove from tray operation.');
        });
    }
});

function toggleDetails(index) {
    var details = document.getElementById('details-' + index);
    var icon = document.querySelector('#item-' + index + ' .expand-btn i');
    if (details.style.display === 'none') {
        details.style.display = 'block';
        icon.classList.remove('fa-chevron-down');
        icon.classList.add('fa-chevron-up');
    } else {
        details.style.display = 'none';
        icon.classList.remove('fa-chevron-up');
        icon.classList.add('fa-chevron-down');
    }
}

function viewDetails(uniqueId) {
    // Use the base URL defined in the HTML and replace the placeholder
    const url = viewStockUrlBase.replace('UNIQUE_ID_PLACEHOLDER', uniqueId);
    window.open(url, '_blank');
}

function duplicateStock(uniqueId) {
    // Use the base URL defined in the HTML and replace the placeholder
    const url = addStockUrlBase.replace('UNIQUE_ID_PLACEHOLDER', uniqueId);
    window.open(url, '_blank');
}

document.addEventListener('DOMContentLoaded', function() {
    updateCart();
    
    // Initialize view mode from saved preference
    toggleView(currentView);
    
    // View toggle button event listeners
    document.getElementById('cardViewBtn').addEventListener('click', function() {
        toggleView('card');
    });
    
    document.getElementById('tableViewBtn').addEventListener('click', function() {
        toggleView('table');
    });
    
    // Table sorting
    document.querySelectorAll('.sortable').forEach(th => {
        th.addEventListener('click', function() {
            sortTable(this.dataset.sort);
        });
    });
    
    // Table row selection and toggle details
    document.querySelectorAll('#tableView tbody tr').forEach(row => {
        row.addEventListener('click', function(event) {
            toggleRowSelection(this, event);
        });
    });
    
    document.querySelectorAll('.show-details-btn').forEach(btn => {
        btn.addEventListener('click', function(event) {
            event.stopPropagation();
            toggleTableDetails(this.dataset.index);
        });
    });
    
    // Select all in table
    document.getElementById('selectAllTable').addEventListener('change', function() {
        const isChecked = this.checked;
        document.querySelectorAll('#tableView tbody input[type="checkbox"]').forEach(checkbox => {
            checkbox.checked = isChecked;
            const row = checkbox.closest('tr');
            if (isChecked) {
                row.classList.add('selected');
            } else {
                row.classList.remove('selected');
            }
            
            // Sync with card view
            const index = checkbox.id.replace('stockTable', '');
            const cardCheckbox = document.getElementById('stock' + index);
            if (cardCheckbox) {
                cardCheckbox.checked = isChecked;
                const card = document.getElementById('item-' + index);
                if (isChecked) {
                    card.classList.add('checked');
                } else {
                    card.classList.remove('checked');
                }
            }
        });
    });
});

document.getElementById('emptyCartBtn').addEventListener('click', function() {
    if (confirm('Are you sure you want to empty the cart?')) {
        emptyCart();
    }
});

function toggleCheckbox(checkboxId, event) {
    const checkbox = document.getElementById(checkboxId);
    if (event.target !== checkbox) {
        checkbox.checked = !checkbox.checked;
    }
    const card = checkbox.closest('.stock-item');
    if (checkbox.checked) {
        card.classList.add('checked');
    } else {
        card.classList.remove('checked');
    }
    
    // Sync with table view
    const index = checkboxId.replace('stock', '');
    const tableCheckbox = document.getElementById('stockTable' + index);
    if (tableCheckbox) {
        tableCheckbox.checked = checkbox.checked;
        if (checkbox.checked) {
            tableCheckbox.closest('tr').classList.add('selected');
        } else {
            tableCheckbox.closest('tr').classList.remove('selected');
        }
    }
}

function toggleCart() {
    const cartContainer = document.querySelector('.floating-cart-container');
    cartContainer.classList.toggle('expanded');
    const icon = cartContainer.querySelector('.toggle-cart-btn i');
    if (cartContainer.classList.contains('expanded')) {
        icon.classList.remove('fa-chevron-up');
        icon.classList.add('fa-chevron-down');
    } else {
        icon.classList.remove('fa-chevron-down');
        icon.classList.add('fa-chevron-up');
    }
}