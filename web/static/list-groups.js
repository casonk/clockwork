document.addEventListener('DOMContentLoaded', () => {
  const root = document.querySelector('[data-list-groups]');
  if (!root) return;

  const categories = [...root.querySelectorAll('.category[data-category]')];
  const categoryFilter = root.querySelector('[data-category-filter]');
  const typeFilter = root.querySelector('[data-type-filter]');
  const search = root.querySelector('[data-list-search]');
  const favoritesOnly = root.querySelector('[data-favorites-only]');
  const priorityFilter = root.querySelector('[data-priority-filter]');
  const inPersonOnly = root.querySelector('[data-in-person-only]');
  const empty = root.querySelector('[data-filter-empty]');
  const status = root.querySelector('[data-filter-status]');
  const storageKey = `clockwork:${root.dataset.listGroups}:open-groups`;
  let openGroups = {};
  try { openGroups = JSON.parse(localStorage.getItem(storageKey) || '{}') || {}; } catch (_) { /* reset invalid saved state */ }

  const savedOpen = (details) => openGroups[details.dataset.collapseKey] ?? true;
  for (const details of root.querySelectorAll('details[data-collapse-key]')) {
    details.open = savedOpen(details);
    details.querySelector(':scope > summary').addEventListener('click', () => {
      openGroups[details.dataset.collapseKey] = !details.open;
      try { localStorage.setItem(storageKey, JSON.stringify(openGroups)); } catch (_) { /* browsing still works */ }
    });
  }

  function applyFilters() {
    const selectedCategory = categoryFilter.value;
    const selectedType = typeFilter.value;
    const query = search ? search.value.trim().toLocaleLowerCase() : '';
    const starred = favoritesOnly?.checked || false;
    const selectedPriority = priorityFilter ? priorityFilter.value : '';
    const inPerson = inPersonOnly?.checked || false;
    const active = Boolean(selectedCategory || selectedType || query || starred || selectedPriority || inPerson);
    const narrowItems = Boolean(selectedType || query || starred || selectedPriority || inPerson);
    let visible = 0;

    for (const category of categories) {
      const categoryAllowed = !selectedCategory || category.dataset.category === selectedCategory;
      const categoryMatches = category.dataset.category.toLocaleLowerCase().includes(query);
      let categoryVisible = 0;
      for (const group of category.querySelectorAll(':scope > .subcategory')) {
        const typeAllowed = !selectedType || group.dataset.type === selectedType;
        const groupMatches = group.dataset.type.toLocaleLowerCase().includes(query);
        let groupVisible = 0;
        for (const item of group.querySelectorAll('.item[data-search-item]')) {
          const textMatches = !query || categoryMatches || groupMatches ||
            item.dataset.searchItem.toLocaleLowerCase().includes(query);
          const show = categoryAllowed && typeAllowed && textMatches &&
            (!starred || item.dataset.favorite === 'true') &&
            (!selectedPriority || item.dataset.priority === selectedPriority) &&
            (!inPerson || item.dataset.inPerson === 'true');
          item.hidden = !show;
          if (show) groupVisible += 1;
        }
        group.hidden = active && groupVisible === 0;
        if (active && groupVisible) group.open = true;
        if (!active) group.open = savedOpen(group);
        categoryVisible += groupVisible;
      }
      category.hidden = !categoryAllowed || (narrowItems && categoryVisible === 0);
      if (categoryAllowed && (selectedCategory || (active && categoryVisible))) category.open = true;
      if (!active) category.open = savedOpen(category);
      visible += categoryVisible;
    }
    empty.hidden = !narrowItems || visible !== 0;
    status.textContent = active ? `${visible} of ${root.dataset.total} items` : '';
  }

  function updateTypeOptions() {
    const selectedCategory = categoryFilter.value;
    for (const option of typeFilter.options) {
      if (!option.value) continue;
      option.disabled = !categories.some(category =>
        (!selectedCategory || category.dataset.category === selectedCategory) &&
        [...category.querySelectorAll(':scope > .subcategory')].some(group => group.dataset.type === option.value)
      );
    }
    if (typeFilter.selectedOptions[0]?.disabled) typeFilter.value = '';
  }

  categoryFilter.addEventListener('change', () => { updateTypeOptions(); applyFilters(); });
  typeFilter.addEventListener('change', applyFilters);
  if (search) search.addEventListener('input', applyFilters);
  if (favoritesOnly) favoritesOnly.addEventListener('change', applyFilters);
  if (priorityFilter) priorityFilter.addEventListener('change', applyFilters);
  if (inPersonOnly) inPersonOnly.addEventListener('change', applyFilters);
  for (const select of root.querySelectorAll('form[data-autosubmit] select')) {
    select.addEventListener('change', () => select.form.requestSubmit());
  }
  updateTypeOptions();
  applyFilters();

  const dialog = document.getElementById('organize-dialog');
  if (dialog) {
    const form = dialog.querySelector('form');
    const field = (name) => form.elements.namedItem(name);
    const categorySelect = field('target_category');
    const typeSelect = field('type');
    const newCategory = field('new_category');
    const newType = field('new_type');
    const syncNewChoice = (select, input, row) => {
      const creating = select.value === '__new__';
      row.hidden = !creating;
      input.required = creating;
      if (!creating) input.value = '';
    };
    categorySelect.addEventListener('change', () =>
      syncNewChoice(categorySelect, newCategory, dialog.querySelector('[data-new-category-row]')));
    typeSelect.addEventListener('change', () =>
      syncNewChoice(typeSelect, newType, dialog.querySelector('[data-new-type-row]')));
    for (const button of root.querySelectorAll('[data-organize-open]')) {
      button.addEventListener('click', () => {
        field('item').value = button.dataset.item;
        field('category').value = button.dataset.category;
        categorySelect.value = button.dataset.category;
        newCategory.value = '';
        const currentType = button.dataset.type;
        if ([...typeSelect.options].some(option => option.value === currentType)) {
          typeSelect.value = currentType;
          newType.value = '';
        } else {
          typeSelect.value = '__new__';
          newType.value = currentType;
        }
        syncNewChoice(categorySelect, newCategory, dialog.querySelector('[data-new-category-row]'));
        syncNewChoice(typeSelect, newType, dialog.querySelector('[data-new-type-row]'));
        dialog.querySelector('[data-organize-title]').textContent = `Organize ${button.dataset.item}`;
        dialog.showModal();
      });
    }
    dialog.querySelector('[data-dialog-close]').addEventListener('click', () => dialog.close());
  }
});
