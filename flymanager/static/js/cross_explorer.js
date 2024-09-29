let cart = JSON.parse(localStorage.getItem('cart')) || [];

function saveCart() {
    localStorage.setItem('cart', JSON.stringify(cart));
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
    window.open('/view_cross/' + uniqueId, '_blank');
}

function duplicateCross(uniqueId) {
    window.open('/add_cross/' + uniqueId, '_blank');
}

document.addEventListener('DOMContentLoaded', function() {
    updateCart();
});

document.getElementById('emptyCartBtn').addEventListener('click', function() {
    if (confirm('Are you sure you want to empty the cart?')) {
        emptyCart();
    }
});
