(function() {
    function isHidden(element) {
        return !element || element.classList.contains('is-hidden');
    }

    function showElement(element, displayMode) {
        if (!element) {
            return;
        }

        element.classList.remove('is-hidden');
        if (displayMode) {
            element.dataset.displayMode = displayMode;
        }
    }

    function hideElement(element) {
        if (!element) {
            return;
        }

        element.classList.add('is-hidden');
    }

    function getLocalDateTime() {
        const now = new Date();
        const offset = now.getTimezoneOffset() * 60000;
        const localTime = new Date(now - offset);
        return localTime.toISOString().slice(0, 16);
    }

    function compareTrayValues(a, b) {
        const emptyA = !a || a.trim() === '';
        const emptyB = !b || b.trim() === '';

        if (emptyA && emptyB) {
            return 0;
        }
        if (emptyA) {
            return 1;
        }
        if (emptyB) {
            return -1;
        }

        const [aPrefix, aSuffix] = (a || '').split('-');
        const [bPrefix, bSuffix] = (b || '').split('-');
        const prefixComparison = aPrefix.toLowerCase().localeCompare(bPrefix.toLowerCase());

        if (prefixComparison !== 0) {
            return prefixComparison;
        }

        const aSuffixNum = aSuffix ? parseInt(aSuffix, 10) : NaN;
        const bSuffixNum = bSuffix ? parseInt(bSuffix, 10) : NaN;

        if (isNaN(aSuffixNum) && isNaN(bSuffixNum)) {
            return 0;
        }
        if (isNaN(aSuffixNum)) {
            return 1;
        }
        if (isNaN(bSuffixNum)) {
            return -1;
        }

        return aSuffixNum - bSuffixNum;
    }

    function createJsonRequest(url, payload) {
        if (!url) {
            return Promise.reject(new Error('Explorer action URL is not configured.'));
        }

        return fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        }).then(function(response) {
            return response.json()
                .catch(function() {
                    return {};
                })
                .then(function(data) {
                    if (!response.ok) {
                        throw new Error(data.message || data.error || 'Request failed.');
                    }
                    return data;
                });
        });
    }

    function bindPerPageControls() {
        document.querySelectorAll('[data-explorer-per-page-select]').forEach(function(select) {
            if (select.dataset.perPageBound === 'true') {
                return;
            }

            select.dataset.perPageBound = 'true';
            select.addEventListener('change', function() {
                const nextValue = String(select.value || '').trim();
                if (!nextValue) {
                    return;
                }

                const nextUrl = new URL(window.location.href);
                nextUrl.searchParams.set('per_page', nextValue);
                nextUrl.searchParams.delete('page');
                window.location.assign(nextUrl.toString());
            });
        });
    }

    function createExplorer(config) {
        let cart = JSON.parse(localStorage.getItem(config.cartStorageKey)) || [];
        let currentView = localStorage.getItem(config.viewStorageKey) || 'card';
        let currentSort = { column: '', direction: 'asc' };
        let edgeDockHideTimer = null;
        let selectedItems = loadSelectedItems();
        const requiredTableColumns = config.requiredTableColumns || [];
        let visibleTableColumns = loadVisibleTableColumns();
        let activeBulkOperation = null;
        let operationStatusResetTimer = null;

        function clearOperationStatusResetTimer() {
            if (operationStatusResetTimer) {
                window.clearTimeout(operationStatusResetTimer);
                operationStatusResetTimer = null;
            }
        }

        function ensureOperationStatusElement() {
            const cartContent = document.querySelector('.cart-content');
            if (!cartContent) {
                return null;
            }

            let statusElement = cartContent.querySelector('[data-explorer-operation-status]');
            if (statusElement) {
                return statusElement;
            }

            statusElement = document.createElement('div');
            statusElement.className = 'explorer-operation-status is-hidden';
            statusElement.setAttribute('data-explorer-operation-status', '');
            statusElement.setAttribute('role', 'status');
            statusElement.setAttribute('aria-live', 'polite');
            cartContent.appendChild(statusElement);
            return statusElement;
        }

        function ensureModalOperationStatusElement() {
            const modalBody = document.querySelector('#bulkFlipModal .modal-body');
            if (!modalBody) {
                return null;
            }

            let statusElement = modalBody.querySelector('[data-explorer-modal-operation-status]');
            if (statusElement) {
                return statusElement;
            }

            statusElement = document.createElement('div');
            statusElement.className = 'explorer-operation-status explorer-operation-status-modal is-hidden';
            statusElement.setAttribute('data-explorer-modal-operation-status', '');
            statusElement.setAttribute('role', 'status');
            statusElement.setAttribute('aria-live', 'polite');
            modalBody.appendChild(statusElement);
            return statusElement;
        }

        function setOperationStatus(message, tone, options) {
            const settings = options || {};
            const elements = [ensureOperationStatusElement()];

            if (settings.includeModal !== false) {
                elements.push(ensureModalOperationStatusElement());
            }

            elements.forEach(function(element) {
                if (!element) {
                    return;
                }

                if (!message) {
                    element.textContent = '';
                    element.classList.add('is-hidden');
                    element.dataset.tone = '';
                    return;
                }

                element.textContent = message;
                element.dataset.tone = tone || 'info';
                element.classList.remove('is-hidden');
            });
        }

        function scheduleOperationStatusReset() {
            clearOperationStatusResetTimer();
            operationStatusResetTimer = window.setTimeout(function() {
                setOperationStatus('', 'info');
            }, 5000);
        }

        function setButtonBusyState(button, isBusy, pendingLabel) {
            if (!button) {
                return;
            }

            if (!button.dataset.defaultLabel) {
                button.dataset.defaultLabel = button.innerHTML;
            }

            button.disabled = isBusy;
            button.classList.toggle('is-busy', isBusy);

            if (isBusy) {
                button.innerHTML = `<span class="explorer-inline-spinner" aria-hidden="true"></span><span>${pendingLabel}</span>`;
                button.setAttribute('aria-busy', 'true');
                return;
            }

            button.innerHTML = button.dataset.defaultLabel;
            button.removeAttribute('aria-busy');
        }

        function setBulkOperationControlsDisabled(isDisabled, operationName, pendingLabel) {
            const bulkFlipBtn = document.getElementById('bulkFlipBtn');
            const bulkStatusBtn = document.getElementById('bulkStatusBtn');
            const removeFromTrayBtn = document.getElementById('removeFromTrayBtn');
            const confirmBulkFlipBtn = document.getElementById('confirmBulkFlipBtn');
            const emptyCartBtn = document.getElementById('emptyCartBtn');
            const generateLabelsBtn = document.getElementById('generateLabelsBtn');
            const bulkFlipModal = document.getElementById('bulkFlipModal');

            [bulkFlipBtn, bulkStatusBtn, removeFromTrayBtn, emptyCartBtn, generateLabelsBtn].forEach(function(button) {
                if (!button) {
                    return;
                }
                button.disabled = isDisabled;
                button.classList.toggle('is-busy', isDisabled && button.id !== 'confirmBulkFlipBtn');
            });

            document.querySelectorAll('.bulk-status-item').forEach(function(item) {
                item.classList.toggle('disabled', isDisabled);
                item.setAttribute('aria-disabled', isDisabled ? 'true' : 'false');
                item.tabIndex = isDisabled ? -1 : 0;
            });

            if (bulkFlipModal) {
                bulkFlipModal.setAttribute('aria-busy', isDisabled ? 'true' : 'false');
                bulkFlipModal.querySelectorAll('[data-dismiss="modal"], .modal-header .close').forEach(function(button) {
                    button.disabled = isDisabled;
                });
            }

            if (confirmBulkFlipBtn) {
                const label = pendingLabel || (operationName ? `${operationName}...` : 'Working...');
                setButtonBusyState(confirmBulkFlipBtn, isDisabled, label);
            }
        }

        function beginBulkOperation(operationKey, progressMessage, pendingLabel) {
            if (activeBulkOperation) {
                setOperationStatus(`${activeBulkOperation.label} is already running. Please wait for it to finish.`, 'warning');
                return false;
            }

            clearOperationStatusResetTimer();
            activeBulkOperation = {
                key: operationKey,
                label: progressMessage,
            };
            setBulkOperationControlsDisabled(true, progressMessage, pendingLabel);
            setOperationStatus(progressMessage, 'progress');
            return true;
        }

        function finishBulkOperation(successMessage, tone, keepVisible) {
            activeBulkOperation = null;
            setBulkOperationControlsDisabled(false);
            setOperationStatus(successMessage, tone || 'success');
            if (!keepVisible) {
                scheduleOperationStatusReset();
            }
        }

        function getSelectionStorageKey() {
            return `${config.viewStorageKey}Selection`;
        }

        function getColumnStorageKey() {
            return `${config.viewStorageKey}Columns`;
        }

        function normalizeSelectionItem(item) {
            if (!item || !item.uid) {
                return null;
            }

            const quantity = Number(item.quantity);
            return {
                id: item.id || item.uid,
                quantity: Number.isFinite(quantity) && quantity > 0 ? quantity : 1,
                type: item.type || config.itemType,
                identifier: item.identifier || '',
                name: item.name || '',
                uid: item.uid,
            };
        }

        function loadSelectedItems() {
            try {
                const storedItems = JSON.parse(sessionStorage.getItem(getSelectionStorageKey()));
                if (!Array.isArray(storedItems)) {
                    return new Map();
                }

                return new Map(
                    storedItems
                        .map(normalizeSelectionItem)
                        .filter(Boolean)
                        .map(function(item) {
                            return [item.uid, item];
                        })
                );
            } catch (error) {
                console.warn('Unable to load explorer selection state:', error);
                return new Map();
            }
        }

        function saveSelectedItems() {
            sessionStorage.setItem(
                getSelectionStorageKey(),
                JSON.stringify(Array.from(selectedItems.values()))
            );
        }

        function getSelectionItemForIndex(index) {
            const card = document.getElementById(`item-${index}`);
            const tableCheckbox = document.getElementById(config.tableCheckboxPrefix + index);
            const row = tableCheckbox ? tableCheckbox.closest('tr') : null;
            const detailsRow = document.getElementById(`tableDetails-${index}`);
            let item = null;

            if (card) {
                item = config.buildCartItemFromCard(index, card);
            } else if (row) {
                item = config.buildCartItemFromTable(index, row, card, detailsRow);
            }

            return normalizeSelectionItem(item);
        }

        function syncSelectAllTableState() {
            const selectAllTable = document.getElementById('selectAllTable');
            if (!selectAllTable) {
                return;
            }

            const pageItems = Array.from(document.querySelectorAll(config.cardCheckboxSelector))
                .map(function(checkbox) {
                    return getSelectionItemForIndex(checkbox.id.replace(config.cardCheckboxPrefix, ''));
                })
                .filter(Boolean);

            selectAllTable.checked = pageItems.length > 0 && pageItems.every(function(item) {
                return selectedItems.has(item.uid);
            });
        }

        function syncSelectionInputsFromState() {
            document.querySelectorAll(config.cardCheckboxSelector).forEach(function(checkbox) {
                const index = checkbox.id.replace(config.cardCheckboxPrefix, '');
                const item = getSelectionItemForIndex(index);
                const isChecked = item ? selectedItems.has(item.uid) : false;
                const tableCheckbox = document.getElementById(config.tableCheckboxPrefix + index);

                checkbox.checked = isChecked;
                setCardState(document.getElementById(`item-${index}`), isChecked);

                if (tableCheckbox) {
                    tableCheckbox.checked = isChecked;
                    setRowState(tableCheckbox.closest('tr'), isChecked);
                }
            });

            syncSelectAllTableState();
        }

        function setSelectedItems(items) {
            selectedItems = new Map(
                items
                    .map(normalizeSelectionItem)
                    .filter(Boolean)
                    .map(function(item) {
                        return [item.uid, item];
                    })
            );
            saveSelectedItems();
            syncSelectionInputsFromState();
        }

        function normalizeVisibleTableColumns(columns) {
            return Array.from(new Set(requiredTableColumns.concat(Array.isArray(columns) ? columns : [])));
        }

        function getDefaultVisibleTableColumns() {
            const compactView = window.matchMedia('(max-width: 1440px)').matches;
            const defaultColumns = compactView && Array.isArray(config.compactTableColumns)
                ? config.compactTableColumns
                : config.defaultTableColumns;

            return normalizeVisibleTableColumns(defaultColumns || []);
        }

        function loadVisibleTableColumns() {
            try {
                const storedColumns = JSON.parse(localStorage.getItem(getColumnStorageKey()));
                if (Array.isArray(storedColumns)) {
                    return normalizeVisibleTableColumns(storedColumns);
                }
            } catch (error) {
                console.warn('Unable to load explorer column preferences:', error);
            }

            return getDefaultVisibleTableColumns();
        }

        function saveVisibleTableColumns() {
            localStorage.setItem(
                getColumnStorageKey(),
                JSON.stringify(visibleTableColumns.filter(function(columnKey) {
                    return requiredTableColumns.indexOf(columnKey) === -1;
                }))
            );
        }

        function applyColumnVisibility() {
            document.querySelectorAll('[data-column-key]').forEach(function(element) {
                const columnKey = element.dataset.columnKey;
                element.classList.toggle('is-column-hidden', visibleTableColumns.indexOf(columnKey) === -1);
            });

            document.querySelectorAll('[data-column-toggle]').forEach(function(input) {
                const columnKey = input.dataset.columnToggle;
                input.checked = visibleTableColumns.indexOf(columnKey) !== -1;
                input.disabled = requiredTableColumns.indexOf(columnKey) !== -1;
            });
        }

        function updateColumnVisibility(columnKey, shouldShow) {
            if (requiredTableColumns.indexOf(columnKey) !== -1) {
                return;
            }

            if (shouldShow && visibleTableColumns.indexOf(columnKey) === -1) {
                visibleTableColumns.push(columnKey);
            }

            if (!shouldShow) {
                visibleTableColumns = visibleTableColumns.filter(function(key) {
                    return key !== columnKey;
                });
            }

            saveVisibleTableColumns();
            applyColumnVisibility();
        }

        function resetColumnVisibility() {
            visibleTableColumns = getDefaultVisibleTableColumns();
            saveVisibleTableColumns();
            applyColumnVisibility();
        }

        function saveCart() {
            localStorage.setItem(config.cartStorageKey, JSON.stringify(cart));
        }

        function saveViewPreference() {
            localStorage.setItem(config.viewStorageKey, currentView);
        }

        function syncTableModeControls() {
            const toolbarControls = document.querySelector('.explorer-toolbar-controls');
            const columnSelector = document.querySelector('.column-selector');

            if (toolbarControls) {
                toolbarControls.classList.toggle('is-table-mode', currentView === 'table');
            }

            if (columnSelector) {
                columnSelector.classList.toggle('is-hidden', currentView !== 'table');
                if (currentView !== 'table') {
                    columnSelector.removeAttribute('open');
                }
            }
        }

        function setCardState(card, isChecked) {
            if (!card) {
                return;
            }

            (config.cardCheckedClasses || ['checked']).forEach(function(className) {
                card.classList.toggle(className, isChecked);
            });
        }

        function setRowState(row, isChecked) {
            if (!row) {
                return;
            }

            row.classList.toggle('selected', isChecked);
        }

        function buildCartItemElement(item, index) {
            const container = document.createElement('div');
            container.className = 'cart-item';

            const label = document.createElement('span');
            const itemType = item.type || config.itemType;
            const typeTag = itemType === 'cross' ? 'Cross' : 'Stock';
            label.textContent = `(${typeTag}) ${item.identifier} - ${item.name}`;

            const quantityInput = document.createElement('input');
            quantityInput.type = 'number';
            quantityInput.className = 'form-control quantity-input';
            quantityInput.value = item.quantity;
            quantityInput.min = '1';
            quantityInput.addEventListener('change', function() {
                updateQuantity(index, this.value);
            });

            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.className = 'btn btn-danger btn-sm';
            removeButton.textContent = 'Remove';
            removeButton.addEventListener('click', function() {
                removeFromCart(index);
            });

            container.appendChild(label);
            container.appendChild(quantityInput);
            container.appendChild(removeButton);
            return container;
        }

        function buildDeleteListItem(item) {
            const li = document.createElement('li');
            li.className = 'list-group-item';

            const strong = document.createElement('strong');
            strong.textContent = item.identifier;

            const nameText = document.createTextNode(` - ${item.name} `);

            const uid = document.createElement('span');
            uid.className = 'text-muted';
            uid.textContent = `(${item.uid})`;

            li.appendChild(strong);
            li.appendChild(nameText);
            li.appendChild(uid);
            return li;
        }

        function getSelectedCheckboxes() {
            return Array.from(selectedItems.values());
        }

        function isElementVisible(element) {
            if (!element) {
                return false;
            }

            return element.getClientRects().length > 0;
        }

        function setSelectionStateForIndex(index, isChecked, persistSelection) {
            const cardCheckbox = document.getElementById(config.cardCheckboxPrefix + index);
            const tableCheckbox = document.getElementById(config.tableCheckboxPrefix + index);
            const item = getSelectionItemForIndex(index);

            if (item) {
                if (isChecked) {
                    selectedItems.set(item.uid, item);
                } else {
                    selectedItems.delete(item.uid);
                }
            }

            if (cardCheckbox) {
                cardCheckbox.checked = isChecked;
                setCardState(document.getElementById(`item-${index}`), isChecked);
            }

            if (tableCheckbox) {
                tableCheckbox.checked = isChecked;
                setRowState(tableCheckbox.closest('tr'), isChecked);
            }

            if (persistSelection !== false) {
                saveSelectedItems();
            }
        }

        function updateDockActionState() {
            const selectButtons = ['selectVisibleDockBtn', 'selectAllDockBtn'].map(function(id) {
                return document.getElementById(id);
            }).filter(Boolean);
            const clearSelectionButton = document.getElementById('clearSelectionDockBtn');
            const totalItems = document.querySelectorAll(config.cardCheckboxSelector).length;
            const selectionCount = getSelectedCheckboxes().length;

            selectButtons.forEach(function(button) {
                button.disabled = totalItems === 0;
            });

            if (clearSelectionButton) {
                clearSelectionButton.disabled = selectionCount === 0;
            }
        }

        function updateScrollDockState() {
            const scrollTopBtn = document.getElementById('scrollTopDockBtn');
            const scrollBottomBtn = document.getElementById('scrollBottomDockBtn');
            const rightDock = document.querySelector('[data-edge-dock="right"]');
            const maxScroll = Math.max(document.documentElement.scrollHeight - window.innerHeight, 0);
            const atTop = window.scrollY <= 24;
            const atBottom = maxScroll <= 24 || window.scrollY >= maxScroll - 24;

            if (scrollTopBtn) {
                scrollTopBtn.classList.toggle('is-hidden', atTop);
            }

            if (scrollBottomBtn) {
                scrollBottomBtn.classList.toggle('is-hidden', atBottom);
            }

            if (rightDock) {
                rightDock.classList.toggle('is-empty', atTop && atBottom);
            }
        }

        function clearEdgeDockHideTimer() {
            if (edgeDockHideTimer !== null) {
                window.clearTimeout(edgeDockHideTimer);
                edgeDockHideTimer = null;
            }
        }

        function scheduleEdgeDockHide() {
            if (!window.matchMedia('(pointer: fine)').matches) {
                return;
            }

            clearEdgeDockHideTimer();
            edgeDockHideTimer = window.setTimeout(function() {
                document.querySelectorAll('.edge-dock').forEach(function(dock) {
                    if (dock.matches(':hover') || dock.contains(document.activeElement)) {
                        return;
                    }

                    dock.classList.remove('is-visible');
                });
            }, 1100);
        }

        function revealEdgeDock(side) {
            const dock = document.querySelector(`[data-edge-dock="${side}"]`);
            if (!dock || dock.classList.contains('is-empty')) {
                return;
            }

            dock.classList.add('is-visible');
            scheduleEdgeDockHide();
        }

        function revealEdgeDocksTemporarily() {
            if (!window.matchMedia('(pointer: fine)').matches) {
                return;
            }

            revealEdgeDock('left');
            revealEdgeDock('right');
        }

        function syncSelectionSummary() {
            const selectionCount = getSelectedCheckboxes().length;
            const selectionCountEl = document.getElementById('selectionCount');
            const selectionCopyEl = document.getElementById('selectionCopy');
            const cartCountInlineEl = document.getElementById('cartCountInline');

            if (selectionCountEl) {
                selectionCountEl.textContent = selectionCount;
            }

            if (selectionCopyEl) {
                selectionCopyEl.textContent = config.selectionMessage(selectionCount);
            }

            if (cartCountInlineEl) {
                cartCountInlineEl.textContent = cart.length;
            }

            updateDockActionState();
        }

        function updateCart() {
            const cartItems = document.getElementById('cartItems');
            if (!cartItems) {
                return;
            }

            cartItems.innerHTML = '';
            if (cart.length === 0) {
                const emptyMessage = document.createElement('p');
                emptyMessage.textContent = 'Your cart is empty.';
                cartItems.appendChild(emptyMessage);
            } else {
                cart.forEach(function(item, index) {
                    cartItems.appendChild(buildCartItemElement(item, index));
                });
            }

            const cartCount = document.getElementById('cartCount');
            if (cartCount) {
                cartCount.textContent = cart.length;
            }

            saveCart();
            syncSelectionSummary();
        }

        function updateQuantity(index, quantity) {
            if (!cart[index]) {
                return;
            }

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

        function toggleView(viewMode) {
            const cardView = document.getElementById('cardView');
            const tableView = document.getElementById('tableView');
            const cardViewBtn = document.getElementById('cardViewBtn');
            const tableViewBtn = document.getElementById('tableViewBtn');

            if (viewMode === 'card') {
                showElement(cardView, 'grid');
                hideElement(tableView);
                cardViewBtn.classList.add('active');
                tableViewBtn.classList.remove('active');
                currentView = 'card';
            } else {
                hideElement(cardView);
                showElement(tableView, 'block');
                cardViewBtn.classList.remove('active');
                tableViewBtn.classList.add('active');
                currentView = 'table';
            }

            syncTableModeControls();
            saveViewPreference();
            syncSelectionInputsFromState();
        }

        function sortTable(column) {
            const table = document.querySelector('#tableView table');
            if (!table) {
                return;
            }

            const headers = table.querySelectorAll('th.sortable');
            const rows = Array.from(table.querySelectorAll('tbody tr.explorer-data-row'));

            if (currentSort.column === column) {
                currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
            } else {
                currentSort.column = column;
                currentSort.direction = 'asc';
            }

            headers.forEach(function(header) {
                header.classList.remove('asc', 'desc');
                if (header.dataset.sort === column) {
                    header.classList.add(currentSort.direction);
                }
            });

            const columnIndex = Array.from(headers).findIndex(function(header) {
                return header.dataset.sort === column;
            }) + 2;

            rows.sort(function(a, b) {
                const aValue = a.querySelector(`td:nth-child(${columnIndex})`).textContent.trim();
                const bValue = b.querySelector(`td:nth-child(${columnIndex})`).textContent.trim();
                let comparison;

                if (column === 'tray') {
                    comparison = compareTrayValues(aValue, bValue);
                } else if (column === 'flipin' || column === 'eclose') {
                    const timeValues = {
                        Overdue: 0,
                        Today: 1,
                        Tomorrow: 2,
                        days: 3,
                    };

                    const aNumDays = aValue.match(/(\d+) days?/);
                    const bNumDays = bValue.match(/(\d+) days?/);
                    let aPriority = 999;
                    let bPriority = 999;

                    Object.entries(timeValues).forEach(function(entry) {
                        const key = entry[0];
                        const value = entry[1];
                        if (aValue.includes(key)) {
                            aPriority = key === 'days' && aNumDays ? value + parseInt(aNumDays[1], 10) : value;
                        }
                        if (bValue.includes(key)) {
                            bPriority = key === 'days' && bNumDays ? value + parseInt(bNumDays[1], 10) : value;
                        }
                    });

                    if (aPriority !== bPriority) {
                        comparison = aPriority - bPriority;
                    } else if (aNumDays && bNumDays) {
                        comparison = parseInt(aNumDays[1], 10) - parseInt(bNumDays[1], 10);
                    } else {
                        comparison = aValue.localeCompare(bValue, undefined, { numeric: true });
                    }
                } else {
                    comparison = aValue.localeCompare(bValue, undefined, { numeric: true });
                }

                return currentSort.direction === 'asc' ? comparison : -comparison;
            });

            const tbody = table.querySelector('tbody');
            rows.forEach(function(row) {
                tbody.appendChild(row);

                const detailsRow = document.getElementById(`tableDetails-${row.dataset.index}`);
                if (detailsRow) {
                    tbody.appendChild(detailsRow);
                }
            });
        }

        function toggleTableCheckbox(checkboxId, event) {
            event.stopPropagation();
            const checkbox = document.getElementById(checkboxId);
            const index = checkboxId.replace(config.tableCheckboxPrefix, '');
            setSelectionStateForIndex(index, checkbox.checked);

            syncSelectionSummary();
        }

        function toggleRowSelection(row, event) {
            if (event.target.tagName === 'INPUT' || event.target.tagName === 'BUTTON' || event.target.closest('button')) {
                return;
            }

            const checkbox = row.querySelector('input[type="checkbox"]');
            const index = checkbox.id.replace(config.tableCheckboxPrefix, '');

            checkbox.checked = !checkbox.checked;
            setSelectionStateForIndex(index, checkbox.checked);

            syncSelectionSummary();
        }

        function toggleTableDetails(index) {
            const details = document.getElementById(`tableDetails-${index}`);
            const trigger = document.querySelector(`.show-details-btn[data-index="${index}"]`);

            if (isHidden(details)) {
                showElement(details, 'table-row');
                if (trigger) {
                    trigger.classList.add('is-active');
                    trigger.setAttribute('aria-expanded', 'true');
                }
            } else {
                hideElement(details);
                if (trigger) {
                    trigger.classList.remove('is-active');
                    trigger.setAttribute('aria-expanded', 'false');
                }
            }
        }

        function initializeColumnControls() {
            const columnInputs = document.querySelectorAll('[data-column-toggle]');
            if (columnInputs.length === 0) {
                return;
            }

            columnInputs.forEach(function(input) {
                input.addEventListener('change', function() {
                    updateColumnVisibility(this.dataset.columnToggle, this.checked);
                });
            });

            document.querySelectorAll('[data-column-reset]').forEach(function(button) {
                button.addEventListener('click', function(event) {
                    event.preventDefault();
                    resetColumnVisibility();
                });
            });

            document.addEventListener('click', function(event) {
                document.querySelectorAll('.column-selector[open]').forEach(function(selector) {
                    if (!selector.contains(event.target)) {
                        selector.removeAttribute('open');
                    }
                });
            });

            applyColumnVisibility();
        }

        function selectVisibleItems() {
            selectAllPageItems();
        }

        function selectAllPageItems() {
            document.querySelectorAll(config.cardCheckboxSelector).forEach(function(checkbox) {
                const index = checkbox.id.replace(config.cardCheckboxPrefix, '');
                setSelectionStateForIndex(index, true, false);
            });

            saveSelectedItems();
            syncSelectAllTableState();
            syncSelectionSummary();
        }

        function fetchAllSelectionItems() {
            if (!config.selectionUrl) {
                return Promise.resolve([]);
            }

            return fetch(config.selectionUrl, {
                method: 'GET',
                headers: {
                    Accept: 'application/json',
                },
                credentials: 'same-origin',
            }).then(function(response) {
                return response.json().then(function(data) {
                    if (!response.ok) {
                        const message = data && (data.error || data.message)
                            ? data.error || data.message
                            : 'Unable to load all matching items.';
                        throw new Error(message);
                    }

                    return Array.isArray(data.items) ? data.items : [];
                });
            });
        }

        function selectAllFilteredItems() {
            const selectAllButton = document.getElementById('selectAllDockBtn');
            const originalLabel = selectAllButton ? selectAllButton.textContent : '';

            if (selectAllButton) {
                selectAllButton.disabled = true;
                selectAllButton.textContent = 'Selecting...';
            }

            fetchAllSelectionItems()
                .then(function(items) {
                    setSelectedItems(items);
                    syncSelectionSummary();
                })
                .catch(function(error) {
                    console.error('Error selecting all matching items:', error);
                    alert(error.message || 'Unable to select all matching items.');
                })
                .finally(function() {
                    if (selectAllButton) {
                        selectAllButton.textContent = originalLabel || 'Select all';
                    }
                    updateDockActionState();
                });
        }

        function mergeCartItem(item) {
            const existingItem = cart.find(function(cartItem) {
                return cartItem.uid === item.uid;
            });

            if (existingItem) {
                existingItem.quantity += 1;
            } else {
                cart.push(item);
            }
        }

        function addSelectedToCart() {
            getSelectedCheckboxes().forEach(function(item) {
                mergeCartItem(item);
            });

            updateCart();
            clearSelection();
        }

        function clearSelection() {
            document.querySelectorAll(config.cardCheckboxSelector).forEach(function(checkbox) {
                const index = checkbox.id.replace(config.cardCheckboxPrefix, '');
                setSelectionStateForIndex(index, false, false);
            });

            selectedItems = new Map();
            saveSelectedItems();
            syncSelectAllTableState();
            syncSelectionSummary();
        }

        function scrollToPageBoundary(position) {
            const top = position === 'top' ? 0 : document.documentElement.scrollHeight;
            window.scrollTo({
                top: top,
                behavior: 'smooth',
            });
        }

        function initializeEdgeDocks() {
            const leftDock = document.querySelector('[data-edge-dock="left"]');
            const rightDock = document.querySelector('[data-edge-dock="right"]');
            const selectVisibleDockBtn = document.getElementById('selectVisibleDockBtn');
            const selectAllDockBtn = document.getElementById('selectAllDockBtn');
            const clearSelectionDockBtn = document.getElementById('clearSelectionDockBtn');
            const scrollTopDockBtn = document.getElementById('scrollTopDockBtn');
            const scrollBottomDockBtn = document.getElementById('scrollBottomDockBtn');

            if (selectVisibleDockBtn) {
                selectVisibleDockBtn.addEventListener('click', function() {
                    selectVisibleItems();
                });
            }

            if (selectAllDockBtn) {
                selectAllDockBtn.addEventListener('click', function() {
                    selectAllFilteredItems();
                });
            }

            if (clearSelectionDockBtn) {
                clearSelectionDockBtn.addEventListener('click', function() {
                    clearSelection();
                });
            }

            if (scrollTopDockBtn) {
                scrollTopDockBtn.addEventListener('click', function() {
                    scrollToPageBoundary('top');
                });
            }

            if (scrollBottomDockBtn) {
                scrollBottomDockBtn.addEventListener('click', function() {
                    scrollToPageBoundary('bottom');
                });
            }

            [leftDock, rightDock].filter(Boolean).forEach(function(dock) {
                dock.addEventListener('mouseenter', function() {
                    clearEdgeDockHideTimer();
                    dock.classList.add('is-visible');
                });

                dock.addEventListener('mouseleave', function() {
                    scheduleEdgeDockHide();
                });

                dock.addEventListener('focusin', function() {
                    clearEdgeDockHideTimer();
                    dock.classList.add('is-visible');
                });

                dock.addEventListener('focusout', function() {
                    scheduleEdgeDockHide();
                });
            });

            document.addEventListener('mousemove', function(event) {
                if (!window.matchMedia('(pointer: fine)').matches) {
                    return;
                }

                if (event.clientX <= 56) {
                    revealEdgeDock('left');
                    return;
                }

                if (event.clientX >= window.innerWidth - 56) {
                    revealEdgeDock('right');
                    return;
                }

                scheduleEdgeDockHide();
            });

            window.addEventListener('scroll', function() {
                updateScrollDockState();
                revealEdgeDocksTemporarily();
            }, { passive: true });

            window.addEventListener('resize', function() {
                updateScrollDockState();
                updateDockActionState();
            });

            updateScrollDockState();
            updateDockActionState();
            scheduleEdgeDockHide();
        }

        function toggleDetails(index) {
            const details = document.getElementById(`details-${index}`);
            if (!details) {
                console.warn('Details element not found:', `details-${index}`);
                return;
            }

            const icon = document.getElementById(`expand-icon-${index}`);

            if (isHidden(details)) {
                showElement(details, 'block');
                if (icon) {
                    icon.classList.remove('fa-chevron-down');
                    icon.classList.add('fa-chevron-up');
                }
            } else {
                hideElement(details);
                if (icon) {
                    icon.classList.remove('fa-chevron-up');
                    icon.classList.add('fa-chevron-down');
                }
            }
        }

        function viewDetails(uniqueId) {
            const url = config.viewUrlBase.replace('UNIQUE_ID_PLACEHOLDER', uniqueId);
            window.open(url, '_blank');
        }

        function duplicateItem(uniqueId) {
            const url = config.duplicateUrlBase.replace('UNIQUE_ID_PLACEHOLDER', uniqueId);
            window.open(url, '_blank');
        }

        function toggleCheckbox(checkboxId, event) {
            const checkbox = document.getElementById(checkboxId);
            if (event.target !== checkbox) {
                checkbox.checked = !checkbox.checked;
            }

            const index = checkboxId.replace(config.cardCheckboxPrefix, '');
            setSelectionStateForIndex(index, checkbox.checked);

            syncSelectionSummary();
        }

        function toggleCart() {
            const cartContainer = document.querySelector('.floating-cart-container');
            if (!cartContainer) {
                console.warn('Cart container element not found');
                return;
            }

            cartContainer.classList.toggle('expanded');

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

        function initializeDeleteModal() {
            const deletePermanentlyBtn = document.getElementById('deletePermanentlyBtn');
            if (deletePermanentlyBtn) {
                deletePermanentlyBtn.addEventListener('click', function() {
                    if (cart.length === 0) {
                        alert('Your cart is empty. Please add items to delete.');
                        return;
                    }

                    const deleteItemCount = document.getElementById('deleteItemCount');
                    deleteItemCount.textContent = cart.length;

                    const deleteItemList = document.getElementById('deleteItemList').querySelector('ul');
                    deleteItemList.innerHTML = '';

                    cart.forEach(function(item) {
                        deleteItemList.appendChild(buildDeleteListItem(item));
                    });

                    document.getElementById('deleteConfirmText').value = '';
                    document.getElementById('confirmDeleteBtn').disabled = true;
                    $('#deleteConfirmModal').modal('show');
                });
            }

            const deleteConfirmText = document.getElementById('deleteConfirmText');
            if (deleteConfirmText) {
                deleteConfirmText.addEventListener('input', function() {
                    const confirmDeleteBtn = document.getElementById('confirmDeleteBtn');
                    confirmDeleteBtn.disabled = deleteConfirmText.value.toUpperCase() !== 'DELETE';
                });
            }

            const confirmDeleteBtn = document.getElementById('confirmDeleteBtn');
            if (confirmDeleteBtn) {
                confirmDeleteBtn.addEventListener('click', function() {
                    if (deleteConfirmText.value.toUpperCase() !== 'DELETE') {
                        alert('Please type "DELETE" to confirm.');
                        return;
                    }

                    createJsonRequest(config.deleteUrl, {
                        uniqueIDs: cart.map(function(item) {
                            return item.uid;
                        }),
                        itemTypes: cart.map(function(item) {
                            return item.type || config.itemType;
                        }),
                    })
                        .then(function(data) {
                            $('#deleteConfirmModal').modal('hide');

                            if (data.success) {
                                alert(config.deleteSuccessMessage(data));
                                emptyCart();
                                window.location.reload();
                                return;
                            }

                            alert(`Error: ${data.message}`);
                        })
                        .catch(function(error) {
                            console.error(`Error during ${config.itemType} deletion:`, error);
                            alert('An error occurred during the deletion process.');
                        });
                });
            }
        }

        function bindStaticEvents() {
            document.querySelectorAll(`${config.itemSelector}[data-checkbox-id]`).forEach(function(card) {
                card.addEventListener('click', function(event) {
                    if (event.target.closest('button, a, input, label')) {
                        return;
                    }

                    toggleCheckbox(this.dataset.checkboxId, event);
                });
            });

            document.querySelectorAll(config.cardCheckboxSelector).forEach(function(checkbox) {
                checkbox.addEventListener('click', function(event) {
                    event.stopPropagation();
                });

                checkbox.addEventListener('change', function() {
                    const index = checkbox.id.replace(config.cardCheckboxPrefix, '');
                    setSelectionStateForIndex(index, checkbox.checked);
                    syncSelectionSummary();
                });
            });

            document.querySelectorAll(config.tableCheckboxSelector).forEach(function(checkbox) {
                checkbox.addEventListener('click', function(event) {
                    toggleTableCheckbox(this.id, event);
                });
            });

            document.querySelectorAll('[data-action="toggle-details"]').forEach(function(button) {
                button.addEventListener('click', function(event) {
                    event.stopPropagation();
                    toggleDetails(this.dataset.index);
                });
            });

            document.querySelectorAll(`[data-action="${config.viewAction}"]`).forEach(function(button) {
                button.addEventListener('click', function(event) {
                    event.stopPropagation();
                    viewDetails(this.dataset.uniqueId);
                });
            });

            document.querySelectorAll(`[data-action="${config.duplicateAction}"]`).forEach(function(button) {
                button.addEventListener('click', function(event) {
                    event.stopPropagation();
                    duplicateItem(this.dataset.uniqueId);
                });
            });

            const cartHeader = document.querySelector('[data-cart-toggle]');
            if (cartHeader) {
                cartHeader.addEventListener('click', function() {
                    toggleCart();
                });
            }

            document.querySelectorAll('[data-cart-control]').forEach(function(button) {
                button.addEventListener('click', function(event) {
                    event.stopPropagation();
                });
            });

            const clearFiltersButton = document.querySelector('button[name="clear_filters"]');
            if (clearFiltersButton) {
                clearFiltersButton.addEventListener('click', function(event) {
                    if (!confirm('Are you sure you want to clear all filters?')) {
                        event.preventDefault();
                    }
                });
            }

            document.getElementById('cardViewBtn').addEventListener('click', function() {
                toggleView('card');
            });

            document.getElementById('tableViewBtn').addEventListener('click', function() {
                toggleView('table');
            });

            const addToCartBtn = document.getElementById('addToCartBtn');
            if (addToCartBtn) {
                addToCartBtn.addEventListener('click', function(event) {
                    event.stopPropagation();
                    addSelectedToCart();
                });
            }

            document.querySelectorAll('.sortable').forEach(function(th) {
                th.addEventListener('click', function() {
                    sortTable(this.dataset.sort);
                });
            });

            document.querySelectorAll('#tableView tbody tr.explorer-data-row').forEach(function(row) {
                row.addEventListener('click', function(event) {
                    toggleRowSelection(this, event);
                });
            });

            document.querySelectorAll('.show-details-btn').forEach(function(btn) {
                btn.addEventListener('click', function(event) {
                    event.stopPropagation();
                    toggleTableDetails(this.dataset.index);
                });
            });

            const selectAllTable = document.getElementById('selectAllTable');
            if (selectAllTable) {
                selectAllTable.addEventListener('change', function() {
                    const isChecked = this.checked;
                    document.querySelectorAll(config.tableCheckboxSelector).forEach(function(checkbox) {
                        const index = checkbox.id.replace(config.tableCheckboxPrefix, '');
                        setSelectionStateForIndex(index, isChecked, false);
                    });
                    saveSelectedItems();
                    syncSelectionSummary();
                });
            }

            const emptyCartBtn = document.getElementById('emptyCartBtn');
            if (emptyCartBtn) {
                emptyCartBtn.addEventListener('click', function() {
                    if (confirm('Are you sure you want to empty the cart?')) {
                        emptyCart();
                    }
                });
            }

            const generateLabelsBtn = document.getElementById('generateLabelsBtn');
            if (generateLabelsBtn) {
                generateLabelsBtn.addEventListener('click', function() {
                    if (cart.length === 0) {
                        alert('Your cart is empty.');
                        return;
                    }

                    document.getElementById('selectedUids').value = cart.map(function(item) {
                        return item.uid;
                    }).join(',');
                    document.getElementById('itemTypes').value = cart.map(function(item) {
                        return item.type || config.itemType;
                    }).join(',');
                    document.getElementById('quantities').value = cart.map(function(item) {
                        return item.quantity;
                    }).join(',');

                    const blankSpacesInput = document.getElementById('blankSpaces');
                    if (blankSpacesInput) {
                        blankSpacesInput.value = '0';
                    }
                    const countNode = document.getElementById('labelItemCount');
                    if (countNode) {
                        countNode.textContent = cart.length;
                    }

                    $('#labelOptionsModal').modal('show');
                });
            }

            const bulkFlipBtn = document.getElementById('bulkFlipBtn');
            if (bulkFlipBtn) {
                bulkFlipBtn.addEventListener('click', function() {
                    if (cart.length === 0) {
                        alert('Your cart is empty. Please add items to flip.');
                        return;
                    }

                    document.getElementById('bulkFlipCount').textContent = cart.length;
                    document.getElementById('bulkFlipTime').value = getLocalDateTime();
                    $('#bulkFlipModal').modal('show');
                });
            }

            const confirmBulkFlipBtn = document.getElementById('confirmBulkFlipBtn');
            if (confirmBulkFlipBtn) {
                confirmBulkFlipBtn.addEventListener('click', function() {
                    if (activeBulkOperation) {
                        setOperationStatus('A bulk explorer action is already running. Please wait for it to finish.', 'warning');
                        return;
                    }

                    if (cart.length === 0) {
                        alert('Your cart is empty.');
                        return;
                    }

                    const flipTime = document.getElementById('bulkFlipTime').value;
                    if (!flipTime) {
                        alert('Please select a flip time.');
                        return;
                    }

                    if (!beginBulkOperation('bulk-flip', `Flipping ${cart.length} item${cart.length === 1 ? '' : 's'}...`, 'Flipping...')) {
                        return;
                    }

                    createJsonRequest(config.bulkFlipUrl, {
                        uniqueIDs: cart.map(function(item) {
                            return item.uid;
                        }),
                        flipTime: flipTime,
                        comment: document.getElementById('bulkFlipComment').value,
                        status: document.getElementById('bulkFlipStatus').value || undefined,
                    })
                        .then(function(data) {
                            $('#bulkFlipModal').modal('hide');
                            // The flip now runs as a background job; the cart is
                            // emptied optimistically and the jobs banner reports
                            // progress and toasts on completion.
                            emptyCart();
                            finishBulkOperation(data.message || 'Bulk flip started in the background.', 'success');
                        })
                        .catch(function(error) {
                            console.error('Error during bulk flip:', error);
                            finishBulkOperation(error.message || 'Error occurred while starting the bulk flip.', 'error', true);
                        });
                });
            }

            document.querySelectorAll('.bulk-status-item').forEach(function(item) {
                item.addEventListener('click', function(event) {
                    event.preventDefault();

                    if (activeBulkOperation) {
                        setOperationStatus('A bulk explorer action is already running. Please wait for it to finish.', 'warning');
                        return;
                    }

                    if (this.getAttribute('aria-disabled') === 'true') {
                        return;
                    }

                    if (cart.length === 0) {
                        alert('Your cart is empty. Please add items to change their status.');
                        return;
                    }

                    const status = this.getAttribute('data-status');
                    if (!confirm(`Are you sure you want to change the status of ${cart.length} items to "${status}"?`)) {
                        return;
                    }

                    if (!beginBulkOperation('bulk-status', `Changing ${cart.length} item${cart.length === 1 ? '' : 's'} to ${status}...`, 'Updating...')) {
                        return;
                    }

                    createJsonRequest(config.bulkStatusUrl, {
                        uniqueIDs: cart.map(function(entry) {
                            return entry.uid;
                        }),
                        status: status,
                    })
                        .then(function(data) {
                            // Runs as a background job now; empty the cart
                            // optimistically and let the jobs banner report on it.
                            emptyCart();
                            finishBulkOperation(data.message || 'Bulk status change started in the background.', 'success');
                        })
                        .catch(function(error) {
                            console.error('Error during bulk status change:', error);
                            finishBulkOperation(error.message || 'Error occurred while starting the bulk status change.', 'error', true);
                        });
                });
            });

            const removeFromTrayBtn = document.getElementById('removeFromTrayBtn');
            if (removeFromTrayBtn) {
                removeFromTrayBtn.addEventListener('click', function() {
                    if (activeBulkOperation) {
                        setOperationStatus('A bulk explorer action is already running. Please wait for it to finish.', 'warning');
                        return;
                    }

                    if (cart.length === 0) {
                        alert('Your cart is empty. Please add items to remove from trays.');
                        return;
                    }

                    if (!confirm(`Are you sure you want to remove ${cart.length} items from their trays?`)) {
                        return;
                    }

                    if (!beginBulkOperation('bulk-remove-from-tray', `Removing ${cart.length} item${cart.length === 1 ? '' : 's'} from trays...`, 'Removing...')) {
                        return;
                    }

                    createJsonRequest(config.bulkRemoveFromTrayUrl, {
                        item_types: cart.map(function(item) {
                            return item.type || config.itemType;
                        }),
                        uniqueIDs: cart.map(function(item) {
                            return item.uid;
                        }),
                    })
                        .then(function(data) {
                            // Runs as a background job now; empty the cart
                            // optimistically. The page is not reloaded immediately
                            // because the removal completes asynchronously - the
                            // jobs banner toasts when it finishes.
                            emptyCart();
                            finishBulkOperation(data.message || 'Tray removal started in the background.', 'success', true);
                        })
                        .catch(function(error) {
                            console.error('Error during bulk remove from tray:', error);
                            finishBulkOperation(error.message || 'Error occurred while starting the bulk remove from tray.', 'error', true);
                        });
                });
            }

            initializeDeleteModal();
        }

        updateCart();
        toggleView(currentView);
        initializeColumnControls();
        bindPerPageControls();
        bindStaticEvents();
        initializeEdgeDocks();
        syncSelectionInputsFromState();
        syncSelectionSummary();
    }

    window.initializeExplorer = function(config) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function handleExplorerReady() {
                document.removeEventListener('DOMContentLoaded', handleExplorerReady);
                createExplorer(config);
            });
            return;
        }

        createExplorer(config);
    };
})();