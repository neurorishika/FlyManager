function buildCrossCardCartItem(index, card) {
    const metaPills = card.querySelectorAll('.cross-meta-pill');

    return {
        id: index,
        quantity: 1,
        identifier: metaPills.length > 0 ? metaPills[0].textContent.trim() : 'No tray',
        name: card.querySelector('h5').textContent.trim(),
        uid: card.querySelector('.cross-item-submeta i').textContent.trim(),
    };
}

function buildCrossTableCartItem(index, row, card) {
    return {
        id: index,
        quantity: 1,
        identifier: row.querySelector('.tray-cell').textContent.trim() || 'No tray',
        name: row.querySelector('td:nth-child(3)').textContent.trim(),
        uid: card.querySelector('.cross-item-submeta i').textContent.trim(),
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
    requiredTableColumns: ['tray', 'name', 'status', 'flipin', 'actions'],
    defaultTableColumns: ['male', 'female', 'species', 'food', 'eclose'],
    compactTableColumns: ['male', 'female'],
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