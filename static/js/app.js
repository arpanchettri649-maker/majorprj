// Major Project 1C — client scripts
document.addEventListener('click', function (event) {
  const toggle = event.target.closest('[data-sidebar-toggle]');
  if (toggle) document.body.classList.toggle('sidebar-open');
  const passwordButton = event.target.closest('[data-toggle-password]');
  if (passwordButton) {
    const input = document.querySelector(passwordButton.dataset.togglePassword);
    if (!input) return;
    input.type = input.type === 'password' ? 'text' : 'password';
    const icon = passwordButton.querySelector('i');
    if (icon) icon.className = input.type === 'password' ? 'bi bi-eye' : 'bi bi-eye-slash';
  }
});
