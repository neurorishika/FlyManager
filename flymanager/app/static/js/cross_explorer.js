let cart = JSON.parse(localStorage.getItem('crossCart')) || [];

function saveCart() {
    localStorage.setItem('crossCart', JSON.stringify(cart));
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

document.getElementById('selectAllBtn').addEventListener('click', function() {
    document.querySelectorAll('.cross-item input[type="checkbox"]').forEach(function(checkbox) {
        checkbox.checked = true;
        document.getElementById('item-' + checkbox.id.replace('cross', '')).classList.add('selected');
    });
});

document.getElementById('deselectAllBtn').addEventListener('click', function() {
    document.querySelectorAll('.cross-item input[type="checkbox"]').forEach(function(checkbox) {
        checkbox.checked = false;
        document.getElementById('item-' + checkbox.id.replace('cross', '')).classList.remove('selected');
    });
});

document.querySelectorAll('.cross-item input[type="checkbox"]').forEach(function(checkbox) {
    checkbox.addEventListener('change', function() {
        if (checkbox.checked) {
            document.getElementById('item-' + checkbox.id.replace('cross', '')).classList.add('selected');
        } else {
            document.getElementById('item-' + checkbox.id.replace('cross', '')).classList.remove('selected');
        }
    });
});

document.querySelector('button[name="clear_filters"]').addEventListener('click', function(event) {
    if (!confirm('Are you sure you want to clear all filters?')) {
        event.preventDefault();
    }
});

document.getElementById('addToCartBtn').addEventListener('click', function() {
    document.querySelectorAll('.cross-item input[type="checkbox"]:checked').forEach(function(checkbox) {
        let index = checkbox.id.replace('cross', '');
        let crossItem = document.getElementById('item-' + index);
        let identifier = crossItem.querySelector('h5').textContent.split('|')[0].trim();
        let name = crossItem.querySelector('h5').textContent.split('|')[1].trim();
        let uid = crossItem.querySelector('p i').textContent.trim();
        let item = {
            id: index,
            quantity: 1,
            identifier: identifier,
            name: name,
            uid: uid
        };

        // Check if item is already in cart
        let existingItem = cart.find(cartItem => cartItem.uid === item.uid);
        if (existingItem) {
            existingItem.quantity++;
        } else {
            cart.push(item);
        }
    });

    updateCart();
    clearSelection();
});

function clearSelection() {
    document.querySelectorAll('.cross-item input[type="checkbox"]').forEach(function(checkbox) {
        checkbox.checked = false;
        document.getElementById('item-' + checkbox.id.replace('cross', '')).classList.remove('selected');
    });
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
                item_type: 'cross',
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
    const url = viewCrossUrlBase.replace('UNIQUE_ID_PLACEHOLDER', uniqueId);
    window.open(url, '_blank');
}

function duplicateCross(uniqueId) {
    // Use the base URL defined in the HTML and replace the placeholder
    const url = addCrossUrlBase.replace('UNIQUE_ID_PLACEHOLDER', uniqueId);
    window.open(url, '_blank');
}

document.addEventListener('DOMContentLoaded', function() {
    updateCart();
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
    const card = checkbox.closest('.cross-item');
    if (checkbox.checked) {
        card.classList.add('checked');
    } else {
        card.classList.remove('checked');
    }
}