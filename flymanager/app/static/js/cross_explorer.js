let cart = JSON.parse(localStorage.getItem('crossCart')) || [];
let currentView = localStorage.getItem('crossViewMode') || 'card';
let currentSort = { column: '', direction: 'asc' };

function isHidden(element) {
    return element.classList.contains('is-hidden');
}

function showElement(element, displayMode) {
    function buildCrossCardCartItem(index, card) {
        const heading = card.querySelector('h5');
        const parts = heading.textContent.split('|');

        return {
            id: index,
            quantity: 1,
            identifier: parts[0].trim() || 'No tray',
            name: parts.slice(1).join('|').trim(),
            uid: card.querySelector('p i').textContent.trim(),
        };
    }

    function buildCrossTableCartItem(index, row, card) {
        return {
            id: index,
            quantity: 1,
            identifier: row.querySelector('.tray-cell').textContent.trim() || 'No tray',
            name: row.querySelector('td:nth-child(3)').textContent.trim(),
            uid: card.querySelector('p i').textContent.trim(),
        };
    }

    window.initializeExplorer({
        itemType: 'cross',
        cartStorageKey: 'crossCart',
        viewStorageKey: 'crossViewMode',
        itemSelector: '.cross-item',
        cardCheckboxSelector: '.cross-selection-checkbox',
        tableCheckboxSelector: '.cross-table-selection-checkbox',
        cardCheckboxPrefix: 'cross',
        tableCheckboxPrefix: 'crossTable',
        cardCheckedClasses: ['selected', 'checked'],
        viewAction: 'view-cross',
        duplicateAction: 'duplicate-cross',
        viewUrlBase: viewCrossUrlBase,
        duplicateUrlBase: addCrossUrlBase,
        deleteUrl: deleteCrossUrl,
        selectionMessage: function(count) {
            return count > 0
                ? `Ready to add ${count} selected cross${count === 1 ? '' : 'es'} to the cart or continue selecting more.`
                : 'Select crosses to add them to the cart or prepare a bulk operation.';
        },
        buildCartItemFromCard: buildCrossCardCartItem,
        buildCartItemFromTable: buildCrossTableCartItem,
        deleteSuccessMessage: function(data) {
            let message = `Successfully deleted ${data.deleted} cross(es).`;
            if (data.skipped > 0) {
                message += ` Skipped ${data.skipped} cross(es) that were not eligible for deletion.`;
            }
            return message;
        },
    });
            tableCheckbox.closest('tr').classList.add('selected');
        } else {
            tableCheckbox.closest('tr').classList.remove('selected');
        }
    }
}

function toggleCart() {
    const cartContainer = document.querySelector('.floating-cart-container');
    if (!cartContainer) {
        console.warn('Cart container element not found');
        return;
    }
    
    cartContainer.classList.toggle('expanded');
    
    // Find the icon by its ID
    const icon = document.getElementById('cartToggleIcon');
    
    if (icon) {
        if (cartContainer.classList.contains('expanded')) {
            icon.classList.remove('fa-chevron-up');
            icon.classList.add('fa-chevron-down');
        } else {
            icon.classList.remove('fa-chevron-down');
            icon.classList.add('fa-chevron-up');
        }
    }
}