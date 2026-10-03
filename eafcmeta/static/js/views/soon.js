// Pagina segnaposto elegante per le sezioni non ancora disponibili.
import { esc, ic } from "../util.js";
import { go } from "../router.js";

export default function soon({ icon, title, lead, points = [] }) {
  return {
    mount(el) {
      el.innerHTML = `<section class="soon"><div class="soon-art" aria-hidden="true"><i class="orb o1"></i><i class="orb o2"></i><div class="soon-ic">${ic(icon, 44)}</div></div>
        <span class="eyebrow">${ic("sparkle", 14)}In arrivo</span>
        <h2 class="soon-t">${esc(title)}</h2><p class="soon-p">${esc(lead)}</p>
        <ul class="soon-l">${points.map((p, i) => `<li style="--i:${i}"><span>${ic("check", 15)}</span>${esc(p)}</li>`).join("")}</ul>
        <div class="soon-s"><span class="pulse"></span>In preparazione: questa sezione si attiverà da sola quando sarà pronta.</div>
        <button class="btn" data-back>${ic("left", 16)}Torna alle carte</button></section>`;
      el.querySelector("[data-back]").onclick = () => go("carte");
    },
    unmount() { },
  };
}
