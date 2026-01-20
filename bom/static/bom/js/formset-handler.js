function initFormset(options) {
    const {
        prefix,
        addBtnId,
        formContainerId,
        emptyFormTemplateId,
        rowSelector,
        onRowAdd
    } = options;

    const totalForms = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
    const formContainer = document.getElementById(formContainerId);
    const emptyFormTemplate = document.getElementById(emptyFormTemplateId).innerHTML;
    const addBtn = document.getElementById(addBtnId);

    if (addBtn) {
        addBtn.addEventListener('click', function (e) {
            if (e) e.preventDefault();
            const currentFormCount = parseInt(totalForms.value);
            const newRowHtml = emptyFormTemplate.replace(/__prefix__/g, currentFormCount);
            
            formContainer.insertAdjacentHTML('beforeend', newRowHtml);
            totalForms.value = currentFormCount + 1;

            const newRow = formContainer.lastElementChild;
            
            // Re-initialize Materialize components
            if (typeof M !== 'undefined') {
                const selects = newRow.querySelectorAll('select');
                if (selects.length > 0) {
                    M.FormSelect.init(selects);
                }
                M.updateTextFields();
            }

            if (onRowAdd) {
                onRowAdd(newRow);
            }
        });
    }

    formContainer.addEventListener('click', function (e) {
        // REMOVE NEW ROW (Client-side only)
        const removeBtn = e.target.closest('.remove-new-row');
        if (removeBtn) {
            e.preventDefault();
            const row = removeBtn.closest(rowSelector);
            row.remove();
            // We don't necessarily decrement totalForms.value here as Django handles missing indexes, 
            // but some implementations do. Given the original code didn't, we won't either unless needed.
        }

        // DELETE EXISTING ROW (Server-side logic via DELETE checkbox)
        const deleteBtn = e.target.closest('.delete-existing-row');
        if (deleteBtn) {
            e.preventDefault();
            const row = deleteBtn.closest(rowSelector);
            const checkbox = row.querySelector('input[type="checkbox"][name$="-DELETE"]');
            if (checkbox) {
                checkbox.checked = true;
                row.style.display = 'none';
            }
        }
    });
}
