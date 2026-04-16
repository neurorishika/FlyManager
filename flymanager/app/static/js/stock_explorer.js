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
    requiredTableColumns: ['tray', 'name', 'status', 'flipin', 'actions'],
    defaultTableColumns: ['series', 'genotype', 'type', 'food', 'species', 'eclose'],
    compactTableColumns: ['series', 'genotype', 'type'],
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