let cart = JSON.parse(localStorage.getItem('cart')) || [];
let currentView = localStorage.getItem('stockViewMode') || 'card';
let currentSort = { column: '', direction: 'asc' };

function isHidden(element) {
    return element.classList.contains('is-hidden');
}

function showElement(element, displayMode) {
    element.classList.remove('is-hidden');
    if (displayMode) {
        element.dataset.displayMode = displayMode;
    }
}

function hideElement(element) {
    element.classList.add('is-hidden');
}

function buildCartItemElement(item, index) {
    const container = document.createElement('div');
    container.className = 'cart-item';

    const label = document.createElement('span');
    label.textContent = `${item.identifier} - ${item.name}`;
function buildStockCardCartItem(index, card) {
    const metaPills = card.querySelectorAll('.stock-meta-pill');

    return {
        id: index,
        quantity: 1,
        identifier: metaPills.length > 0 ? metaPills[0].textContent.trim() : 'Unassigned',
        name: card.querySelector('h5').textContent.trim(),
        uid: card.querySelector('.stock-item-submeta i').textContent.trim(),
    };
}

function buildStockTableCartItem(index, row, card) {
    const seriesId = row.querySelector('td:nth-child(3)').textContent.trim();

    return {
        id: index,
        quantity: 1,
        identifier: row.querySelector('.tray-cell').textContent.trim() || `${seriesId} / No tray`,
        name: row.querySelector('td:nth-child(4)').textContent.trim(),
        uid: card.querySelector('.stock-item-submeta i').textContent.trim(),
    };
}

window.initializeExplorer({
    itemType: 'stock',
    cartStorageKey: 'cart',
    viewStorageKey: 'stockViewMode',
    itemSelector: '.stock-item',
    cardCheckboxSelector: '.stock-selection-checkbox',
    tableCheckboxSelector: '.stock-table-selection-checkbox',
    cardCheckboxPrefix: 'stock',
    tableCheckboxPrefix: 'stockTable',
    cardCheckedClasses: ['checked'],
    viewAction: 'view-stock',
    duplicateAction: 'duplicate-stock',
    viewUrlBase: viewStockUrlBase,
    duplicateUrlBase: addStockUrlBase,
    deleteUrl: deleteStockUrl,
    selectionMessage: function(count) {
        return count > 0
            ? `Ready to add ${count} selected stock${count === 1 ? '' : 's'} to the cart or continue selecting more.`
            : 'Select stocks to add them to the cart or prepare a bulk operation.';
    },
    buildCartItemFromCard: buildStockCardCartItem,
    buildCartItemFromTable: buildStockTableCartItem,
    deleteSuccessMessage: function(data) {
        let message = `Successfully deleted ${data.deleted} item(s).`;
        if (data.skipped > 0) {
            message += ` Skipped ${data.skipped} item(s) that were not eligible for deletion.`;
        }
        return message;
    },
});
            icon.classList.remove('fa-chevron-down');
            icon.classList.add('fa-chevron-up');
        }
    }
}