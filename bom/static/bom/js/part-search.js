// Suggests parts as a part number is typed into an input with data-part-search="<search url>". The input keeps its
// name and value, so the form posts the same part number with or without this script.
(function () {
    'use strict';

    function attach(input) {
        const wrapper = document.createElement('div');
        const listbox = document.createElement('ul');
        const status = document.createElement('span');
        wrapper.className = 'part-search';
        listbox.className = 'part-search-options';
        listbox.id = input.id + '-options';
        listbox.setAttribute('role', 'listbox');
        listbox.hidden = true;
        status.className = 'part-search-status';
        status.setAttribute('role', 'status');
        input.parentNode.insertBefore(wrapper, input);
        wrapper.append(input, listbox, status);
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-controls', listbox.id);
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('autocomplete', 'off');

        let results = [];
        let active = -1;
        let timer = null;
        let latestRequest = 0;

        function close() {
            listbox.hidden = true;
            input.setAttribute('aria-expanded', 'false');
            input.removeAttribute('aria-activedescendant');
            active = -1;
        }

        function render(parts) {
            results = parts;
            active = -1;
            listbox.replaceChildren(...parts.map(function (part, index) {
                const option = document.createElement('li');
                const number = document.createElement('strong');
                const description = document.createElement('span');
                const outcome = document.createElement('small');
                option.id = listbox.id + '-' + index;
                option.setAttribute('role', 'option');
                option.setAttribute('aria-selected', 'false');
                if (!part.addable) {
                    option.setAttribute('aria-disabled', 'true');
                }
                number.textContent = part.part_number;
                description.textContent = part.description;
                outcome.textContent = part.outcome;
                option.append(number, ' ', description, outcome);
                option.addEventListener('mousedown', function (event) {
                    event.preventDefault();
                    choose(index);
                });
                return option;
            }));
            status.textContent = parts.length === 1 ? '1 part found' : parts.length + ' parts found';
            listbox.hidden = !parts.length;
            input.setAttribute('aria-expanded', String(Boolean(parts.length)));
            input.removeAttribute('aria-activedescendant');
        }

        function highlight(index) {
            const options = listbox.children;
            if (active >= 0) {
                options[active].setAttribute('aria-selected', 'false');
            }
            active = index;
            options[active].setAttribute('aria-selected', 'true');
            options[active].scrollIntoView({block: 'nearest'});
            input.setAttribute('aria-activedescendant', options[active].id);
        }

        function choose(index) {
            if (results[index].addable) {
                input.value = results[index].part_number;
                close();
            }
        }

        function search() {
            const query = input.value.trim();
            const request = ++latestRequest;
            if (!query) {
                results = [];
                close();
                return;
            }
            fetch(input.dataset.partSearch + '?q=' + encodeURIComponent(query), {headers: {Accept: 'application/json'}})
                .then(function (response) { return response.ok ? response.json() : {parts: []}; })
                .then(function (data) {
                    if (request === latestRequest && document.activeElement === input) {
                        render(data.parts);
                    }
                })
                .catch(close);
        }

        input.addEventListener('input', function () {
            clearTimeout(timer);
            timer = setTimeout(search, 250);
        });
        input.addEventListener('blur', close);
        input.addEventListener('keydown', function (event) {
            if (listbox.hidden) {
                return;
            }
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                const step = event.key === 'ArrowDown' ? 1 : -1;
                highlight(active < 0 && step < 0 ? results.length - 1 : (active + step + results.length) % results.length);
            } else if (event.key === 'Enter' && active >= 0) {
                event.preventDefault();
                choose(active);
            } else if (event.key === 'Escape') {
                event.preventDefault();
                close();
            }
        });
    }

    document.querySelectorAll('input[data-part-search]').forEach(attach);
})();
