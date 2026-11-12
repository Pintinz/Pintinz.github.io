/* Campus forms (campus/ambassadors.html, campus/sessions.html): validate, post to the CareerOS API
   as text/plain JSON (no CORS preflight), show success or a clear error. */
(function () {
  var API = location.hostname === 'pintinz.github.io'
    ? 'https://p01--careeros-api--szyhwlzghlw2.code.run/api/v1/campus/'
    : (window.CAREEROS_API || 'http://localhost:8000/api/v1/campus/');

  function data(form) {
    var out = {};
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || el.type === 'submit') return;
      if (el.type === 'checkbox') {
        if (!out[el.name]) out[el.name] = [];
        if (el.checked) out[el.name].push(el.value);
        return;
      }
      if (el.value.trim()) out[el.name] = el.value.trim();
    });
    return out;
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-campus-form]'), function (form) {
    var status = form.querySelector('[data-form-status]');
    var button = form.querySelector('button[type=submit]');
    var done = form.parentNode.querySelector('[data-form-done]');

    function say(text, bad) {
      status.textContent = text;
      status.classList.toggle('is-error', !!bad);
    }

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var firstBad = Array.prototype.find.call(form.elements, function (el) { return el.willValidate && !el.checkValidity(); });
      if (firstBad) {
        var label = form.querySelector('label[for="' + firstBad.id + '"]');
        say('Please check: ' + (label ? label.firstChild.textContent.trim() : 'the highlighted field') + '.', true);
        firstBad.focus();
        return;
      }
      button.disabled = true;
      say('Sending…');
      fetch(API + form.getAttribute('data-campus-form'), {
        method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: JSON.stringify(data(form))
      }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (body) { return { ok: res.ok, body: body, status: res.status }; });
      }).then(function (r) {
        if (r.ok) {
          form.hidden = true;
          done.hidden = false;
          done.focus();
          return;
        }
        say(r.status === 429 ? 'Too many attempts. Please wait a while and try again.'
          : (r.body && r.body.detail) || 'That didn’t send. Please try again.', true);
        button.disabled = false;
      }).catch(function () {
        say('That didn’t send. Check your connection and try again, or email careeros.info@gmail.com.', true);
        button.disabled = false;
      });
    });
  });
})();
