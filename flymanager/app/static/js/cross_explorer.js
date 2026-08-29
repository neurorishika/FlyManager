function buildCrossCardCartItem(index, card) {
    const metaPills = card.querySelectorAll('.cross-meta-pill');

    return {
        id: index,
        quantity: 1,
        type: 'cross',
        identifier: metaPills.length > 0 ? metaPills[0].textContent.trim() : 'No tray',
        name: card.querySelector('h5').textContent.trim(),
        uid: card.querySelector('.cross-item-submeta i').textContent.trim(),
    };
}

function buildCrossTableCartItem(index, row, card) {
    return {
        id: index,
        quantity: 1,
        type: 'cross',
        identifier: row.querySelector('.tray-cell').textContent.trim() || 'No tray',
        name: row.querySelector('td:nth-child(3)').textContent.trim(),
        uid: card.querySelector('.cross-item-submeta i').textContent.trim(),
    };
}

window.initializeExplorer({
    itemType: 'cross',
    cartStorageKey: 'explorerCart',
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
    selectionUrl: crossSelectionUrl,
    deleteUrl: deletePermanentlyUrl,
    bulkFlipUrl: bulkFlipUrl,
    bulkStatusUrl: bulkStatusUrl,
    bulkRemoveFromTrayUrl: bulkRemoveFromTrayUrl,
    requiredTableColumns: ['tray', 'name', 'status', 'flipin', 'actions'],
    defaultTableColumns: ['male', 'female', 'phenotype', 'species', 'food', 'eclose'],
    compactTableColumns: ['male', 'female', 'phenotype'],
    selectionMessage: function(count) {
        return count > 0
            ? `Ready to add ${count} selected cross${count === 1 ? '' : 'es'} to the cart or continue selecting more.`
            : 'Select crosses to add them to the cart or prepare a bulk operation.';
    },
    buildCartItemFromCard: buildCrossCardCartItem,
    buildCartItemFromTable: buildCrossTableCartItem,
    deleteSuccessMessage: function(data) {
        let message = `Successfully deleted ${data.deleted} item(s).`;
        if (data.skipped > 0) {
            message += ` Skipped ${data.skipped} item(s) that were not eligible for deletion.`;
        }
        return message;
    },
});