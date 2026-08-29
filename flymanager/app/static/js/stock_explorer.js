function buildStockCardCartItem(index, card) {
    const metaPills = card.querySelectorAll('.stock-meta-pill');

    return {
        id: index,
        quantity: 1,
        type: 'stock',
        identifier: metaPills.length > 0 ? metaPills[0].textContent.trim() : 'Unassigned',
        name: card.querySelector('h5').textContent.trim(),
        uid: card.querySelector('.stock-item-submeta i').textContent.trim(),
    };
}

function parseJsonDataset(value, fallbackValue) {
    if (!value) {
        return fallbackValue;
    }

    try {
        const parsed = JSON.parse(value);
        return parsed == null ? fallbackValue : parsed;
    } catch (error) {
        console.warn('Unable to parse cached provider match payload:', error);
        return fallbackValue;
    }
}

function buildStockOrderQueueItem(index, card, row) {
    const metaPills = card.querySelectorAll('.stock-meta-pill');
    const fallbackIdentifier = row
        ? row.querySelector('.tray-cell').textContent.trim() || 'Unassigned'
        : (metaPills.length > 0 ? metaPills[0].textContent.trim() : 'Unassigned');

    return {
        id: index,
        quantity: 1,
        identifier: fallbackIdentifier,
        name: card.querySelector('h5').textContent.trim(),
        uid: card.querySelector('.stock-item-submeta i').textContent.trim(),
        sourceType: card.dataset.sourceType || '',
        sourceID: card.dataset.sourceId || '',
        sourceCollection: card.dataset.sourceCollection || '',
        flyBaseStockID: card.dataset.flybaseStockId || '',
        providerUrl: card.dataset.providerUrl || '',
        providerLinkLabel: card.dataset.providerLinkLabel || '',
        providerLinkKind: card.dataset.providerLinkKind || '',
        note: '',
        matches: parseJsonDataset(card.dataset.providerMatches, []),
        matchesLoaded: card.dataset.providerMatchesLoaded === 'true',
        matchesCachedAt: card.dataset.providerMatchesCachedAt || '',
        matchesFromCache: card.dataset.providerMatchesCached === 'true',
        matchesCountLabel: card.dataset.providerMatchesCountLabel || '',
        matchesStatusLabel: card.dataset.providerMatchesStatusLabel || '',
        matchesPrimaryLabel: card.dataset.providerMatchesPrimaryLabel || '',
        matchesError: '',
    };
}

function buildStockTableCartItem(index, row, card) {
    const seriesId = row.querySelector('td:nth-child(3)').textContent.trim();

    return {
        id: index,
        quantity: 1,
        type: 'stock',
        identifier: row.querySelector('.tray-cell').textContent.trim() || `${seriesId} / No tray`,
        name: row.querySelector('td:nth-child(4)').textContent.trim(),
        uid: card.querySelector('.stock-item-submeta i').textContent.trim(),
    };
}

window.initializeExplorer({
    itemType: 'stock',
    cartStorageKey: 'explorerCart',
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
    selectionUrl: stockSelectionUrl,
    deleteUrl: deletePermanentlyUrl,
    bulkFlipUrl: bulkFlipUrl,
    bulkStatusUrl: bulkStatusUrl,
    bulkRemoveFromTrayUrl: bulkRemoveFromTrayUrl,
    requiredTableColumns: ['tray', 'name', 'status', 'flipin', 'actions'],
    defaultTableColumns: ['series', 'genotype', 'phenotype', 'type', 'food', 'species', 'eclose'],
    compactTableColumns: ['series', 'genotype', 'phenotype'],
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

(function() {
    const orderQueueStorageKey = 'stockOrderQueue';

    function getReverseSearchUrl(uid) {
        return reverseSearchUrlBase.replace('UNIQUE_ID_PLACEHOLDER', encodeURIComponent(uid));
    }

    function fetchReverseSearch(uid, options) {
        const config = options || {};
        const searchUrl = new URL(getReverseSearchUrl(uid), window.location.origin);
        if (config.refresh) {
            searchUrl.searchParams.set('refresh', '1');
        }

        return fetch(searchUrl.toString())
            .then(function(response) {
                return response.json().then(function(data) {
                    if (!response.ok) {
                        const message = data && (data.error || data.message)
                            ? data.error || data.message
                            : 'Reverse search failed.';
                        throw new Error(message);
                    }
                    return data;
                });
            });
    }

    function createJsonRequest(url, payload) {
        return fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        }).then(function(response) {
            return response.json().then(function(data) {
                if (!response.ok) {
                    const message = data && (data.message || data.error)
                        ? data.message || data.error
                        : 'Request failed.';
                    throw new Error(message);
                }
                return data;
            });
        });
    }

    function getCurrentExplorerView() {
        const tableView = document.getElementById('tableView');
        if (tableView && !tableView.classList.contains('is-hidden')) {
            return 'table';
        }
        return 'card';
    }

    function loadOrderQueue() {
        try {
            const parsed = JSON.parse(localStorage.getItem(orderQueueStorageKey));
            return Array.isArray(parsed) ? parsed : [];
        } catch (error) {
            console.warn('Unable to load stock order queue:', error);
            return [];
        }
    }

    function saveOrderQueue(queue) {
        localStorage.setItem(orderQueueStorageKey, JSON.stringify(queue));
    }

    function getOrderQueue() {
        return loadOrderQueue();
    }

    function setOrderQueue(queue) {
        saveOrderQueue(queue);
        renderOrderQueue();
    }

    function mergeOrderQueueItem(queue, item) {
        const existingItem = queue.find(function(queueItem) {
            return queueItem.uid === item.uid;
        });

        if (existingItem) {
            existingItem.quantity += 1;
            if (!existingItem.providerUrl && item.providerUrl) {
                existingItem.providerUrl = item.providerUrl;
                existingItem.providerLinkLabel = item.providerLinkLabel;
                existingItem.providerLinkKind = item.providerLinkKind;
            }
            if (!existingItem.sourceType && item.sourceType) {
                existingItem.sourceType = item.sourceType;
                existingItem.sourceID = item.sourceID;
                existingItem.sourceCollection = item.sourceCollection;
                existingItem.flyBaseStockID = item.flyBaseStockID;
            }
            if (!existingItem.matchesLoaded && item.matchesLoaded) {
                existingItem.matches = item.matches;
                existingItem.matchesLoaded = true;
                existingItem.matchesCachedAt = item.matchesCachedAt;
                existingItem.matchesFromCache = item.matchesFromCache;
                existingItem.matchesCountLabel = item.matchesCountLabel;
                existingItem.matchesStatusLabel = item.matchesStatusLabel;
                existingItem.matchesPrimaryLabel = item.matchesPrimaryLabel;
            }
            return;
        }

        queue.push(item);
    }

    function shouldAutoSearchMatches(item) {
        return Boolean(item && item.uid && !item.providerUrl && !item.matchesLoaded && !item.matchesLoading);
    }

    function syncOrderQueueMatchResults(currentItem, data) {
        currentItem.matches = data.candidates || [];
        currentItem.matchesLoaded = true;
        currentItem.matchesLoading = false;
        currentItem.matchesError = '';
        currentItem.matchesCachedAt = data.cachedAt || '';
        currentItem.matchesFromCache = Boolean(data.cached);
        currentItem.matchesCountLabel = data.cacheCountLabel || '';
        currentItem.matchesStatusLabel = data.cacheStatusLabel || '';
        currentItem.matchesPrimaryLabel = data.primaryMatchLabel || '';
        if (!currentItem.providerUrl && currentItem.matches.length > 0 && currentItem.matches[0].providerURL) {
            currentItem.providerUrl = currentItem.matches[0].providerURL;
            currentItem.providerLinkLabel = currentItem.matches[0].providerLinkLabel || 'Open matched provider page';
            currentItem.providerLinkKind = currentItem.matches[0].providerLinkKind || 'matched';
        }
    }

    function syncOrderQueueMatchError(currentItem, error) {
        currentItem.matches = [];
        currentItem.matchesLoaded = true;
        currentItem.matchesLoading = false;
        currentItem.matchesError = error.message;
        currentItem.matchesCachedAt = '';
        currentItem.matchesFromCache = false;
        currentItem.matchesCountLabel = '';
        currentItem.matchesStatusLabel = '';
        currentItem.matchesPrimaryLabel = '';
    }

    function loadOrderQueueMatches(index, options) {
        const currentQueue = getOrderQueue();
        const currentItem = currentQueue[index];
        if (!currentItem || !currentItem.uid || currentItem.matchesLoading) {
            return Promise.resolve();
        }

        currentItem.matchesLoading = true;
        currentItem.matchesError = '';
        saveOrderQueue(currentQueue);
        renderOrderQueue();

        return fetchReverseSearch(currentItem.uid, options)
            .then(function(data) {
                syncOrderQueueMatchResults(currentItem, data);
                saveOrderQueue(currentQueue);
                renderOrderQueue();
            })
            .catch(function(error) {
                syncOrderQueueMatchError(currentItem, error);
                saveOrderQueue(currentQueue);
                renderOrderQueue();
            });
    }

    function getSelectedOrderQueueItems() {
        const currentView = getCurrentExplorerView();
        const checkedItems = currentView === 'card'
            ? document.querySelectorAll('.stock-selection-checkbox:checked')
            : document.querySelectorAll('.stock-table-selection-checkbox:checked');

        return Array.from(checkedItems).map(function(checkbox) {
            const index = checkbox.id.replace(currentView === 'card' ? 'stock' : 'stockTable', '');
            const card = document.getElementById(`item-${index}`);
            const row = checkbox.closest('tr');
            return buildStockOrderQueueItem(index, card, row);
        });
    }

    function buildOrderQueueMatchList(matches) {
        const wrapper = document.createElement('div');
        wrapper.className = 'order-queue-match-list';

        matches.forEach(function(match) {
            const item = document.createElement('div');
            item.className = 'order-queue-match-item';

            const meta = document.createElement('div');
            meta.className = 'order-queue-match-meta';

            const title = document.createElement('strong');
            title.textContent = `${match.sourceCollection || match.stockSource} ${match.sourceID}`;

            const subtitle = document.createElement('p');
            subtitle.className = 'order-queue-match-copy';
            subtitle.textContent = `${match.name || 'Unnamed stock'} • Score ${match.matchScore}`;

            const reasons = document.createElement('p');
            reasons.className = 'order-queue-match-copy';
            reasons.textContent = (match.matchReasons || []).join(' • ');

            meta.appendChild(title);
            meta.appendChild(subtitle);
            meta.appendChild(reasons);

            item.appendChild(meta);

            if (match.providerURL) {
                const link = document.createElement('a');
                link.className = 'btn btn-outline-info btn-sm';
                link.href = match.providerURL;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.textContent = 'Open';
                item.appendChild(link);
            }

            wrapper.appendChild(item);
        });

        return wrapper;
    }

    function renderOrderQueue() {
        const queue = getOrderQueue();
        const container = document.getElementById('orderQueueItems');
        const count = document.getElementById('orderQueueCount');
        if (!container || !count) {
            return;
        }

        count.textContent = queue.length;
        container.innerHTML = '';

        if (queue.length === 0) {
            const emptyState = document.createElement('p');
            emptyState.className = 'order-queue-empty';
            emptyState.textContent = 'No stocks queued for ordering yet.';
            container.appendChild(emptyState);
            return;
        }

        queue.forEach(function(item, index) {
            const entry = document.createElement('div');
            entry.className = 'order-queue-item';

            const header = document.createElement('div');
            header.className = 'order-queue-item-header';

            const summary = document.createElement('div');
            summary.className = 'order-queue-item-summary';

            const title = document.createElement('h3');
            title.className = 'order-queue-item-title';
            title.textContent = item.name;

            const meta = document.createElement('p');
            meta.className = 'order-queue-item-meta';
            meta.textContent = item.sourceType && item.sourceID
                ? `${item.sourceType} ${item.sourceID}${item.sourceCollection ? ` • ${item.sourceCollection}` : ''}`
                : 'No external provider linked yet';

            const secondary = document.createElement('p');
            secondary.className = 'order-queue-item-meta';
            secondary.textContent = `${item.identifier} • ${item.uid}`;

            summary.appendChild(title);
            summary.appendChild(meta);
            summary.appendChild(secondary);

            const actions = document.createElement('div');
            actions.className = 'order-queue-item-actions';

            if (item.providerUrl) {
                const providerLink = document.createElement('a');
                providerLink.className = 'btn btn-outline-info btn-sm';
                providerLink.href = item.providerUrl;
                providerLink.target = '_blank';
                providerLink.rel = 'noopener noreferrer';
                providerLink.textContent = 'Open Provider';
                providerLink.title = item.providerLinkLabel || 'Open source link';
                actions.appendChild(providerLink);
            }

            const matchesButton = document.createElement('button');
            matchesButton.type = 'button';
            matchesButton.className = 'btn btn-outline-secondary btn-sm';
            matchesButton.textContent = item.matchesLoading
                ? 'Searching...'
                : (item.matchesLoaded ? 'Refresh Matches' : 'Find Matches');
            matchesButton.disabled = Boolean(item.matchesLoading);
            matchesButton.addEventListener('click', function() {
                loadOrderQueueMatches(index, { refresh: item.matchesLoaded });
            });
            actions.appendChild(matchesButton);

            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.className = 'btn btn-outline-danger btn-sm';
            removeButton.textContent = 'Remove';
            removeButton.addEventListener('click', function() {
                const currentQueue = getOrderQueue();
                currentQueue.splice(index, 1);
                setOrderQueue(currentQueue);
            });
            actions.appendChild(removeButton);

            header.appendChild(summary);
            header.appendChild(actions);
            entry.appendChild(header);

            const fields = document.createElement('div');
            fields.className = 'order-queue-field-grid';

            const quantityField = document.createElement('label');
            quantityField.className = 'order-queue-field';
            quantityField.textContent = 'Quantity';

            const quantityInput = document.createElement('input');
            quantityInput.type = 'number';
            quantityInput.className = 'form-control';
            quantityInput.min = '1';
            quantityInput.value = item.quantity || 1;
            quantityInput.addEventListener('change', function() {
                const currentQueue = getOrderQueue();
                if (!currentQueue[index]) {
                    return;
                }
                currentQueue[index].quantity = Math.max(1, parseInt(quantityInput.value, 10) || 1);
                saveOrderQueue(currentQueue);
            });
            quantityField.appendChild(quantityInput);

            const noteField = document.createElement('label');
            noteField.className = 'order-queue-field order-queue-field-wide';
            noteField.textContent = 'Order Note';

            const noteInput = document.createElement('textarea');
            noteInput.className = 'form-control';
            noteInput.rows = 2;
            noteInput.placeholder = 'Vendor contact, PO, planned order date, or handling notes';
            noteInput.value = item.note || '';
            noteInput.addEventListener('input', function() {
                const currentQueue = getOrderQueue();
                if (!currentQueue[index]) {
                    return;
                }
                currentQueue[index].note = noteInput.value;
                saveOrderQueue(currentQueue);
            });
            noteField.appendChild(noteInput);

            fields.appendChild(quantityField);
            fields.appendChild(noteField);
            entry.appendChild(fields);

            if (item.matchesLoaded) {
                const matchesSection = document.createElement('div');
                matchesSection.className = 'order-queue-matches';

                const matchesTitle = document.createElement('p');
                matchesTitle.className = 'order-queue-match-title';
                matchesTitle.textContent = item.providerUrl
                    ? 'Reverse Search Candidates'
                    : 'Suggested Provider Matches';
                matchesSection.appendChild(matchesTitle);

                if (item.matchesCountLabel || item.matchesStatusLabel || item.matchesPrimaryLabel) {
                    const cacheMeta = document.createElement('div');
                    cacheMeta.className = 'order-queue-match-meta';

                    if (item.matchesCountLabel || item.matchesStatusLabel) {
                        const summary = document.createElement('p');
                        summary.className = 'order-queue-match-copy';
                        summary.textContent = [item.matchesCountLabel, item.matchesStatusLabel].filter(Boolean).join(' • ');
                        cacheMeta.appendChild(summary);
                    }

                    if (item.matchesPrimaryLabel) {
                        const primary = document.createElement('p');
                        primary.className = 'order-queue-match-copy';
                        primary.textContent = `Top match: ${item.matchesPrimaryLabel}`;
                        cacheMeta.appendChild(primary);
                    }

                    matchesSection.appendChild(cacheMeta);
                }

                if (item.matchesLoading) {
                    const loadingMessage = document.createElement('p');
                    loadingMessage.className = 'order-queue-match-copy';
                    loadingMessage.textContent = 'Searching provider catalogs for candidate matches...';
                    matchesSection.appendChild(loadingMessage);
                } else if (item.matchesError) {
                    const errorMessage = document.createElement('p');
                    errorMessage.className = 'order-queue-match-error';
                    errorMessage.textContent = item.matchesError;
                    matchesSection.appendChild(errorMessage);
                } else if (item.matches && item.matches.length > 0) {
                    matchesSection.appendChild(buildOrderQueueMatchList(item.matches));
                } else {
                    const emptyMatches = document.createElement('p');
                    emptyMatches.className = 'order-queue-match-copy';
                    emptyMatches.textContent = 'No external provider matches were found for this stock.';
                    matchesSection.appendChild(emptyMatches);
                }

                entry.appendChild(matchesSection);
            }

            container.appendChild(entry);

            if (shouldAutoSearchMatches(item)) {
                loadOrderQueueMatches(index);
            }
        });
    }

    function clearOrderQueue() {
        if (!confirm('Are you sure you want to clear the order queue?')) {
            return;
        }
        setOrderQueue([]);
    }

    function addSelectedToOrderQueue() {
        const selectedItems = getSelectedOrderQueueItems();
        if (selectedItems.length === 0) {
            alert('Select at least one stock before adding to the order queue.');
            return;
        }

        const queue = getOrderQueue();
        selectedItems.forEach(function(item) {
            mergeOrderQueueItem(queue, item);
        });

        setOrderQueue(queue);
        alert(`Queued ${selectedItems.length} stock${selectedItems.length === 1 ? '' : 's'} for ordering. Missing provider links will auto-search for candidate matches.`);
    }

    function openAllProviderLinks() {
        const queue = getOrderQueue();
        const uniqueUrls = Array.from(new Set(queue.map(function(item) {
            return item.providerUrl;
        }).filter(Boolean)));

        if (uniqueUrls.length === 0) {
            alert('No provider links are available for the current order queue. Use reverse search first if needed.');
            return;
        }

        uniqueUrls.forEach(function(url) {
            window.open(url, '_blank', 'noopener');
        });
    }

    function markQueuedOrdered() {
        const queue = getOrderQueue();
        if (queue.length === 0) {
            alert('The order queue is empty.');
            return;
        }

        if (!confirm(`Mark ${queue.length} queued stock${queue.length === 1 ? '' : 's'} as Ordered and append the current order notes?`)) {
            return;
        }

        createJsonRequest(markOrderedUrl, {
            items: queue.map(function(item) {
                return {
                    uid: item.uid,
                    note: item.note || '',
                };
            }),
        })
            .then(function(data) {
                const successfulUids = new Set((data.results && data.results.success || []).map(function(item) {
                    return item.uid;
                }));
                const remainingQueue = queue.filter(function(item) {
                    return !successfulUids.has(item.uid);
                });

                setOrderQueue(remainingQueue);
                alert(data.message);
                if (successfulUids.size > 0) {
                    window.location.reload();
                }
            })
            .catch(function(error) {
                console.error('Error marking queued stocks ordered:', error);
                alert(error.message || 'Unable to update queued stocks.');
            });
    }

    document.addEventListener('DOMContentLoaded', function() {
        if (!document.getElementById('orderQueueItems')) {
            return;
        }

        renderOrderQueue();

        const addSelectedButton = document.getElementById('addSelectedToOrderQueueBtn');
        if (addSelectedButton) {
            addSelectedButton.addEventListener('click', addSelectedToOrderQueue);
        }

        const openAllButton = document.getElementById('openAllProviderLinksBtn');
        if (openAllButton) {
            openAllButton.addEventListener('click', openAllProviderLinks);
        }

        const markOrderedButton = document.getElementById('markQueuedOrderedBtn');
        if (markOrderedButton) {
            markOrderedButton.addEventListener('click', markQueuedOrdered);
        }

        const clearQueueButton = document.getElementById('clearOrderQueueBtn');
        if (clearQueueButton) {
            clearQueueButton.addEventListener('click', clearOrderQueue);
        }
    });
})();