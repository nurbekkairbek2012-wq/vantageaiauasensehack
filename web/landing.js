/* Лендинг: подсветка активного раздела при прокрутке.
 *
 * Вынесено в файл, а не написано инлайном в HTML, чтобы политика
 * безопасности могла остаться строгой: script-src 'self' без
 * 'unsafe-inline'. Инлайновый скрипт потребовал бы ослабить её для
 * всего сайта ради восьми строк.
 */

'use strict';

const links = [...document.querySelectorAll('.nav-links a')];

const observer = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    const target = '#' + entry.target.id;
    links.forEach((a) => a.classList.toggle('on', a.getAttribute('href') === target));
  }
}, { rootMargin: '-45% 0px -50% 0px' });

document.querySelectorAll('section[id]').forEach((section) => observer.observe(section));
