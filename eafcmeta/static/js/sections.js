// Registro delle sezioni dell'app. Per aggiungere una pagina: creare views/<nome>.js che esporta
// `default function view()` con { mount(el), unmount() } e aggiungere una riga qui sotto.
import cards from "./views/cards.js";
import compare from "./views/compare.js";
import importer from "./views/import.js";
import calibrate from "./views/calibrate.js";
import rules from "./views/rules.js";
import research from "./views/research.js";
import auto from "./views/auto.js";

export const SECTIONS = [
  { id: "carte", label: "Carte", icon: "cards", group: "Valuta", title: "Carte", sub: "La tua collezione, valutata", view: cards },
  { id: "confronta", label: "Confronta", icon: "compare", group: "Valuta", title: "Confronta", sub: "Da 2 a 3 carte affiancate", view: compare },
  { id: "importa", label: "Importa", icon: "import", group: "Dati", title: "Importa", sub: "Carte, prezzi e pagine salvate", view: importer },
  { id: "calibra", label: "Calibra", icon: "calibrate", group: "Dati", title: "Calibra", sub: "Soglie e pesi dal meta dei pro", view: calibrate },
  { id: "regole", label: "Regole", icon: "rules", group: "Motore", title: "Regole", sub: "Come l'app giudica una carta", view: rules },
  { id: "ricerca", label: "Ricerca pareri", icon: "research", group: "Motore", title: "Ricerca pareri", sub: "Proposte dei pro, con la tua conferma", view: research },
  { id: "automatico", label: "Automatico", icon: "sync", group: "Motore", title: "Automatico", sub: "Raccolta dei pareri da YouTube", view: auto },
];
