(() => {
  const nav = document.querySelector('.cw-site-nav');
  if (!nav) return;
  const groups = [...nav.querySelectorAll('.cw-site-nav__group')];
  groups.forEach(group => {
    group.addEventListener('toggle', () => {
      if (group.open) groups.forEach(other => { if (other !== group) other.open = false; });
    });
  });
  document.addEventListener('click', event => {
    if (!nav.contains(event.target)) groups.forEach(group => { group.open = false; });
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      const open = groups.find(group => group.open);
      if (open) {
        open.open = false;
        open.querySelector('summary').focus();
      }
    }
  });
})();
