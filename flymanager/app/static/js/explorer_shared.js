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
        return fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        }).then(function(response) {
            return response.json();
        });
    }

    function createExplorer(config) {
        let cart = JSON.parse(localStorage.getItem(config.cartStorageKey)) || [];
        let currentView = localStorage.getItem(config.viewStorageKey) || 'card';
        let currentSort = { column: '', direction: 'asc' };

        function saveCart() {
            localStorage.setItem(config.cartStorageKey, JSON.stringify(cart));
        }

        function saveViewPreference() {
            localStorage.setItem(config.viewStorageKey, currentView);
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
            label.textContent = `${item.identifier} - ${item.name}`;

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
            return Array.from(document.querySelectorAll(`${config.cardCheckboxSelector}:checked`));
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

            saveViewPreference();
        }

        function sortTable(column) {
            const table = document.querySelector('#tableView table');
            if (!table) {
                return;
            }

            const headers = table.querySelectorAll('th.sortable');
            const rows = Array.from(table.querySelectorAll('tbody tr'));

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
            });
        }

        function toggleTableCheckbox(checkboxId, event) {
            event.stopPropagation();
            const checkbox = document.getElementById(checkboxId);
            const row = checkbox.closest('tr');
            const index = checkboxId.replace(config.tableCheckboxPrefix, '');
            const cardCheckbox = document.getElementById(config.cardCheckboxPrefix + index);
            const card = document.getElementById(`item-${index}`);

            setRowState(row, checkbox.checked);

            if (cardCheckbox) {
                cardCheckbox.checked = checkbox.checked;
                setCardState(card, checkbox.checked);
            }

            syncSelectionSummary();
        }

        function toggleRowSelection(row, event) {
            if (event.target.tagName === 'INPUT' || event.target.tagName === 'BUTTON' || event.target.closest('button')) {
                return;
            }

            const checkbox = row.querySelector('input[type="checkbox"]');
            const index = checkbox.id.replace(config.tableCheckboxPrefix, '');
            const cardCheckbox = document.getElementById(config.cardCheckboxPrefix + index);
            const card = document.getElementById(`item-${index}`);

            checkbox.checked = !checkbox.checked;
            setRowState(row, checkbox.checked);

            if (cardCheckbox) {
                cardCheckbox.checked = checkbox.checked;
                setCardState(card, checkbox.checked);
            }

            syncSelectionSummary();
        }

        function toggleTableDetails(index) {
            const details = document.getElementById(`tableDetails-${index}`);

            if (isHidden(details)) {
                showElement(details, 'block');
            } else {
                hideElement(details);
            }
        }

        function selectVisibleItems() {
            if (currentView === 'card') {
                document.querySelectorAll(config.cardCheckboxSelector).forEach(function(checkbox) {
                    const index = checkbox.id.replace(config.cardCheckboxPrefix, '');
                    checkbox.checked = true;
                    setCardState(document.getElementById(`item-${index}`), true);
                });
            } else {
                const selectAllTable = document.getElementById('selectAllTable');
                if (selectAllTable) {
                    selectAllTable.checked = true;
                }

                document.querySelectorAll(config.tableCheckboxSelector).forEach(function(checkbox) {
                    const index = checkbox.id.replace(config.tableCheckboxPrefix, '');
                    const cardCheckbox = document.getElementById(config.cardCheckboxPrefix + index);
                    checkbox.checked = true;
                    setRowState(checkbox.closest('tr'), true);
                    if (cardCheckbox) {
                        cardCheckbox.checked = true;
                        setCardState(document.getElementById(`item-${index}`), true);
                    }
                });
            }

            syncSelectionSummary();
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
            const checkedItems = currentView === 'card'
                ? document.querySelectorAll(`${config.cardCheckboxSelector}:checked`)
                : document.querySelectorAll(`${config.tableCheckboxSelector}:checked`);

            checkedItems.forEach(function(checkbox) {
                const index = checkbox.id.replace(currentView === 'card' ? config.cardCheckboxPrefix : config.tableCheckboxPrefix, '');
                const card = document.getElementById(`item-${index}`);
                const row = checkbox.closest('tr');
                const item = currentView === 'card'
                    ? config.buildCartItemFromCard(index, card)
                    : config.buildCartItemFromTable(index, row, card, document.getElementById(`tableDetails-${index}`));

                mergeCartItem(item);
            });

            updateCart();
            clearSelection();
        }

        function clearSelection() {
            document.querySelectorAll(config.cardCheckboxSelector).forEach(function(checkbox) {
                const index = checkbox.id.replace(config.cardCheckboxPrefix, '');
                checkbox.checked = false;
                setCardState(document.getElementById(`item-${index}`), false);
            });

            document.querySelectorAll(config.tableCheckboxSelector).forEach(function(checkbox) {
                checkbox.checked = false;
                setRowState(checkbox.closest('tr'), false);
            });

            const selectAllTable = document.getElementById('selectAllTable');
            if (selectAllTable) {
                selectAllTable.checked = false;
            }

            syncSelectionSummary();
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

            const card = checkbox.closest(config.itemSelector);
            const index = checkboxId.replace(config.cardCheckboxPrefix, '');
            const tableCheckbox = document.getElementById(config.tableCheckboxPrefix + index);

            setCardState(card, checkbox.checked);

            if (tableCheckbox) {
                tableCheckbox.checked = checkbox.checked;
                setRowState(tableCheckbox.closest('tr'), checkbox.checked);
            }

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
                    const tableCheckbox = document.getElementById(config.tableCheckboxPrefix + index);
                    setCardState(document.getElementById(`item-${index}`), checkbox.checked);
                    if (tableCheckbox) {
                        tableCheckbox.checked = checkbox.checked;
                        setRowState(tableCheckbox.closest('tr'), checkbox.checked);
                    }
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

            const selectAllBtn = document.getElementById('selectAllBtn');
            if (selectAllBtn) {
                selectAllBtn.addEventListener('click', function(event) {
                    event.stopPropagation();
                    selectVisibleItems();
                });
            }

            const addToCartBtn = document.getElementById('addToCartBtn');
            if (addToCartBtn) {
                addToCartBtn.addEventListener('click', function(event) {
                    event.stopPropagation();
                    addSelectedToCart();
                });
            }

            const deselectAllBtn = document.getElementById('deselectAllBtn');
            if (deselectAllBtn) {
                deselectAllBtn.addEventListener('click', function(event) {
                    event.stopPropagation();
                    clearSelection();
                });
            }

            document.querySelectorAll('.sortable').forEach(function(th) {
                th.addEventListener('click', function() {
                    sortTable(this.dataset.sort);
                });
            });

            document.querySelectorAll('#tableView tbody tr').forEach(function(row) {
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
                        const cardCheckbox = document.getElementById(config.cardCheckboxPrefix + index);
                        checkbox.checked = isChecked;
                        setRowState(checkbox.closest('tr'), isChecked);
                        if (cardCheckbox) {
                            cardCheckbox.checked = isChecked;
                            setCardState(document.getElementById(`item-${index}`), isChecked);
                        }
                    });
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

                    const blankSpaces = prompt('How many blank spaces should be left?');
                    if (blankSpaces !== null) {
                        document.getElementById('selectedUids').value = cart.map(function(item) {
                            return item.uid;
                        }).join(',');
                        document.getElementById('blankSpaces').value = blankSpaces;
                        document.getElementById('quantities').value = cart.map(function(item) {
                            return item.quantity;
                        }).join(',');
                        document.getElementById('generateLabelsForm').submit();
                    }

                    emptyCart();
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
                    if (cart.length === 0) {
                        alert('Your cart is empty.');
                        return;
                    }

                    const flipTime = document.getElementById('bulkFlipTime').value;
                    if (!flipTime) {
                        alert('Please select a flip time.');
                        return;
                    }

                    createJsonRequest(window.bulkFlipUrl, {
                        uniqueIDs: cart.map(function(item) {
                            return item.uid;
                        }),
                        flipTime: flipTime,
                        comment: document.getElementById('bulkFlipComment').value,
                        status: document.getElementById('bulkFlipStatus').value || undefined,
                    })
                        .then(function(data) {
                            $('#bulkFlipModal').modal('hide');
                            alert(data.message);
                            if (data.results && data.results.success && data.results.success.length > 0) {
                                emptyCart();
                            }
                        })
                        .catch(function(error) {
                            console.error('Error during bulk flip:', error);
                            alert('Error occurred during bulk flip operation.');
                        });
                });
            }

            document.querySelectorAll('.bulk-status-item').forEach(function(item) {
                item.addEventListener('click', function(event) {
                    event.preventDefault();

                    if (cart.length === 0) {
                        alert('Your cart is empty. Please add items to change their status.');
                        return;
                    }

                    const status = this.getAttribute('data-status');
                    if (!confirm(`Are you sure you want to change the status of ${cart.length} items to "${status}"?`)) {
                        return;
                    }

                    createJsonRequest(window.bulkStatusUrl, {
                        uniqueIDs: cart.map(function(entry) {
                            return entry.uid;
                        }),
                        status: status,
                    })
                        .then(function(data) {
                            alert(data.message);
                            if (data.results && data.results.success && data.results.success.length > 0) {
                                emptyCart();
                            }
                        })
                        .catch(function(error) {
                            console.error('Error during bulk status change:', error);
                            alert('Error occurred during bulk status change operation.');
                        });
                });
            });

            const removeFromTrayBtn = document.getElementById('removeFromTrayBtn');
            if (removeFromTrayBtn) {
                removeFromTrayBtn.addEventListener('click', function() {
                    if (cart.length === 0) {
                        alert('Your cart is empty. Please add items to remove from trays.');
                        return;
                    }

                    if (!confirm(`Are you sure you want to remove ${cart.length} items from their trays?`)) {
                        return;
                    }

                    createJsonRequest(window.bulkRemoveFromTrayUrl, {
                        item_type: config.itemType,
                        uniqueIDs: cart.map(function(item) {
                            return item.uid;
                        }),
                    })
                        .then(function(data) {
                            alert(data.message);
                            if (data.results && data.results.success && data.results.success.length > 0) {
                                emptyCart();
                            }
                            window.location.reload();
                        })
                        .catch(function(error) {
                            console.error('Error during bulk remove from tray:', error);
                            alert('Error occurred during bulk remove from tray operation.');
                        });
                });
            }

            initializeDeleteModal();
        }

        updateCart();
        toggleView(currentView);
        bindStaticEvents();
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