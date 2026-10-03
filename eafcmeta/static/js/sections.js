// Registro delle sezioni dell'app. Per aggiungere una pagina (es. "Regole" o "Ricerca pareri"
// quando arriveranno le API): creare views/<nome>.js che esporta `default function view()`
// con { mount(el), unmount() } e sostituire il segnaposto `soon(...)` con la nuova vista.
import cards from "./views/cards.js";
import compare from "./views/compare.js";
import importer from "./views/import.js";
import calibrate from "./views/calibrate.js";
import soon from "./views/soon.js";

export const SECTIONS = [
  { id: "carte", label: "Carte", icon: "cards", group: "Valuta", title: "Carte", sub: "La tua collezione, valutata", view: cards },
  { id: "confronta", label: "Confronta", icon: "compare", group: "Valuta", title: "Confronta", sub: "Da 2 a 3 carte affiancate", view: compare },
  { id: "importa", label: "Importa", icon: "import", group: "Dati", title: "Importa", sub: "Carte, prezzi e pagine salvate", view: importer },
  { id: "calibra", label: "Calibra", icon: "calibrate", group: "Dati", title: "Calibra", sub: "Soglie e pesi dal meta dei pro", view: calibrate },
  { id: "regole", label: "Regole", icon: "rules", group: "In arrivo", soon: true, title: "Regole", sub: "Il motore dei giudizi", view: () => soon({
    icon: "rules", title: "Regole del motore",
    lead: "Qui vedrai e approverai le regole che trasformano statistiche, PlayStyle e body type in un giudizio.",
    points: ["Ogni regola con il suo peso e le prove che la sostengono", "Regole proposte dai pareri dei pro, da accettare o rifiutare", "«Cosa ha contato» in ogni carta, passo per passo"] }) },
  { id: "ricerca", label: "Ricerca pareri", icon: "research", group: "In arrivo", soon: true, title: "Ricerca pareri", sub: "Proposte dei pro", view: () => soon({
    icon: "research", title: "Proposte dei pro",
    lead: "Qui arriveranno le proposte di pareri trovate o incollate da te: le controlli e, solo se confermi, diventano pareri della carta.",
    points: ["Incolla un post o una trascrizione e ricevi una proposta con la fonte", "Ogni proposta resta in attesa finché non la approvi", "Niente viene salvato senza la tua conferma"] }) },
];
