/*! tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages */
// German explanation texts for the info popovers. Numbers come from router.json "thresholds" so the page never states a stale limit.
import { fmtPct } from './model.js?v=20261102d';

export function explain(key, th = {}) {
  const T = {
    spend: {
      title: 'OpenRouter-Abrechnung',
      short: 'Was OpenRouter auf dem Schlüssel tatsächlich abgerechnet hat - nicht die Rechnung des Routers.',
      body: [
        '„24 h", „Monat" und „gesamt" sind die echten Abrechnungszahlen des OpenRouter-Schlüssels. Dort fließen alle Modelle und alle Programme ein, die diesen Schlüssel nutzen.',
        'Wichtig: OpenRouter setzt seine Tageszählung um 00:00 UTC zurück. Damit „24 h" wirklich 24 Stunden meint, rechnet die Seite den Zuwachs des Gesamtzählers zwischen zwei Messungen aus - kein Kalendertag, sondern ein rollierendes Fenster.',
        'Die Kacheln darüber („heute erwartet") sind Router-Arithmetik: gemessene Token mal Listenpreis des laut Entscheidung erwarteten Anbieters. Die Angabe „× der Router-Rechnung" vergleicht beides für dieselben 24 Stunden.',
        'Der Faktor ist selten 1: auf dem Schlüssel laufen auch andere Modelle und Programme, und nur für den Teil des Verkehrs, dessen Anbieter wirklich feststeht, kann der Router rechnen. Wie groß dieser Anteil ist, steht unter „Router-Rechnung für dieselben 24 h" und im Vertrauens-Panel.',
        '„Tagesbudget übrig" ist der Rest bis zum Schlüssel-Limit. OpenRouter setzt dieses Limit täglich zurück (siehe Feld limit_reset), deshalb ist es kein aufgespartes Guthaben.',
        'Solange die Seite aktuell ist, ist der Abrechnungs-Block durchgezogen umrandet; bei veralteten Daten wird er gepunktet und mit „Stand …" markiert.',
      ],
    },
    cmp: {
      title: 'Platz 1 gegen Platz 2',
      short: 'Der günstigste Anbieter und die nächstbeste Wahl für den heutigen Verbrauch - mit Abstand.',
      body: [
        'Beide Zahlen sind für den heutigen Verbrauch gerechnet: Prompt-Token des Tages mal Preis je 1M Prompt inklusive Output-Anteil und gemessener Cache-Quote.',
        'Platz 2 ist nicht automatisch die bessere Wahl. Die Standardwahl wechselt erst, wenn ein anderer Anbieter mindestens den eingestellten Vorsprung günstiger ist (Schwelle unter „So entscheidet der Router").',
        'Bei laufenden Sitzungen kommt der warme Cache dazu: ein Wechsel würde den Kontext einmal ungecacht neu einlesen, das kann teurer sein als der Preisvorteil.',
        'Die Prozent-Angabe am Ende nennt den Aufschlag von Platz 2 gegenüber Platz 1 und die Mehrkosten pro Tag beim heutigen Verbrauch.',
      ],
    },
    watch: {
      title: 'Anbieter im Blick',
      short: 'Wer war schon der günstigste - und wann wird er es wieder?',
      body: [
        'Jeder Anbieter, der für unseren Verbrauch mindestens einmal Platz 1 war, kommt auf die Liste. War er sieben Tage lang nicht mehr der günstigste, verschwindet er wieder.',
        'Die Linien zeigen den Preis je 1M Prompt-Token im Verlauf, gerechnet mit dem heutigen Verbrauchsprofil (Cache-Quote, Output-Anteil). So sind alte und neue Preise vergleichbar.',
        '„Billige Phasen" sind Zeiträume, in denen ein Anbieter deutlich unter seinem eigenen Wochen-Median lag. Genau dort lohnt sich ein Blick, denn die Anzeige oben reagiert nur auf den aktuellen Preis.',
        'Klick auf die Legende blendet einen Anbieter aus oder ein, Klick auf eine Zeile hebt ihn hervor und zeigt seine Auswertung. Die Überwachung läuft, solange der Router Preise abruft.',
      ],
    },
    expectation: {
      title: 'Erwartung gegen Messung',
      short: 'Was der Router annimmt, gegen das, was wirklich passiert.',
      body: [
        'Der Router entscheidet nur aufgrund von Annahmen: Wie viel vom Prompt kommt aus dem Cache, wie viele Token fallen pro Tag an, was kostet ein Anbieter.',
        'Diese Seite stellt jede Annahme der tatsächlichen Messung gegenüber. Stimmt beides überein, ist die Entscheidung gut begründet. Weicht es ab, ist das ein Hinweis, dass das Modell nachgeschärft werden muss.',
        '„Erwartung“ ist gestrichelt oder als Umriss gezeichnet, „Messung“ durchgezogen oder gefüllt. Der Status steht immer als Wort und Symbol da, nicht nur als Farbe.',
        'Was OpenRouter tatsächlich abgerechnet hat, steht separat im Hero unter „OpenRouter abrechnet“ - das ist die echte Abrechnung, keine Router-Rechnung.',
      ],
    },
    cache: {
      title: 'Cache-Quote',
      short: 'Anteil des Prompts, der aus dem Anbieter-Cache kommt (viel billiger).',
      body: [
        'Bei langen Agenten-Sitzungen wird fast der ganze Verlauf wieder mitgeschickt. Liest der Anbieter ihn aus dem Cache, kostet das nur einen Bruchteil des normalen Input-Preises.',
        `Erwartung: die Quote, mit der der Router gerechnet hat. Messung: Cache-Read geteilt durch Prompt der Anfragen, bei denen der Anbieter bekannt ist (letzte 24 h).`,
        `Status: ok bis ${th.hit_ok_pp ?? 3} Prozentpunkte Abweichung, beobachten bis ${th.hit_watch_pp ?? 8}, darüber abweichend.`,
      ],
    },
    tokens: {
      title: 'Token pro Tag',
      short: 'Prognose des Tagesverbrauchs gegen den echten Verbrauch.',
      body: [
        'Die Prognose ist der Tagesverbrauch, den der Router am Morgen des Tages angenommen hat (Durchschnitt der letzten Woche). Gemessen wird die Summe aller Prompt-Token des Tages.',
        'Der Verbrauch schwankt stark (Arbeitstage, Wochenende). Deshalb sind die Schwellen weit: ' + `ok bis ${fmtPct(th.tok_ok ?? 0.25)}, beobachten bis ${fmtPct(th.tok_watch ?? 0.5)}.`,
        'Der heutige Tag ist noch nicht abgeschlossen und zählt nicht zur Bewertung.',
      ],
    },
    cost: {
      title: 'Kosten: erwartet gegen gemessen',
      short: 'Preismodell gegen den echten Verbrauch des API-Schlüssels.',
      body: [
        'Erwartet: echte Token mal Listenpreis des Anbieters, der die Anfrage bedient hat.',
        'Intern gemessen: Zuwachs des Verbrauchszählers des API-Schlüssels zwischen zwei Abfragen (nur dieser Schlüssel). Der Zähler hinkt etwa eine Minute hinterher, deshalb wird stundenweise zusammengefasst.',
        `Stimmen beide überein, hat tatsächlich der erwartete Anbieter zum erwarteten Preis abgerechnet. Status: ok bis ${fmtPct(th.cost_ok ?? 0.1)}, beobachten bis ${fmtPct(th.cost_watch ?? 0.25)}.`,
        'Es werden nur Zeiträume ausgewertet, in denen der Zähler regelmäßig abgefragt wurde (bei Pausen ohne Nutzung gibt es keine Messung).',
      ],
    },
    regret: {
      title: 'Rückblick (Regret)',
      short: 'Was der gewählte Anbieter im Nachhinein mehr gekostet hat als der beste.',
      body: [
        'Für jeden Tag werden alle echten Anfragen mit den Preisen jedes zulässigen Anbieters nachgerechnet. Regret ist die Differenz zwischen dem, was der gewählte Anbieter gekostet hat, und dem günstigsten.',
        `Null heißt: Der Router hat im Nachhinein die beste Wahl getroffen. Status (Regret relativ zum Besten): ok bis ${fmtPct(th.reg_ok ?? 0.05)}, beobachten bis ${fmtPct(th.reg_watch ?? 0.15)}.`,
      ],
    },
    cache_ttl: {
      title: `Warum ${th.cache_ttl_min ?? 10} Minuten?`,
      short: 'So lange hält der Anbieter-Cache nach der letzten Anfrage.',
      body: [
        `OpenRouter hält die Zuordnung „Gespräch → Anbieter“ ${th.cache_ttl_min ?? 10} Minuten nach der letzten Anfrage warm. Danach ist der Cache weg: Die nächste Anfrage bezahlt den ganzen Verlauf zum normalen Input-Preis, egal bei welchem Anbieter.`,
        'Konsequenz: Nach einer Pause von mehr als dieser Zeit kostet ein Wechsel nichts zusätzlich. Dann übernimmt die Sitzung sofort die aktuell beste Wahl.',
      ],
    },
    switching: {
      title: 'Wie der Router entscheidet',
      short: 'Keine feste Wartezeit. Preise und Rechnung entscheiden.',
      body: [
        `1. Standardwahl (für neue und lange pausierte Sitzungen): der günstigste Anbieter. Er wird nur gewechselt, wenn ein anderer mindestens ${fmtPct(th.rel_gain_min ?? 0.1)} günstiger ist. Das verhindert Hin und Her bei kleinen Preisunterschieden (Hysterese, gilt in beide Richtungen).`,
        `2. Laufende Sitzungen mit heißem Cache bleiben bei ihrem Anbieter, solange ein Wechsel sich nicht lohnt. Ein Wechsel kostet einmal den ungecachten Verlauf. Er lohnt sich, wenn er sich innerhalb von ${th.payback_max_min ?? 60} Minuten aktiver Nutzung wieder einspielt und der Anbieter mindestens ${fmtPct(th.rel_gain_min ?? 0.1)} teurer ist.`,
        '3. Wird ein Anbieter plötzlich teuer, ist das eine Preis-Flanke: Sie wird protokolliert und der Router rechnet sofort neu. Eine laufende Sitzung wechselt dann, sobald die Rechnung aufgeht, ohne Mindestlaufzeit.',
        '4. Fällt ein Anbieter aus oder wird unzulässig (zu geringe Verfügbarkeit), wechseln alle sofort.',
      ],
    },
    hysteresis: {
      title: 'Hysterese',
      short: 'Ein Wechsel braucht einen echten Vorsprung.',
      body: [
        `Zwei Anbieter, die sich um 1 Cent unterscheiden, sind kein Grund zum Wechseln: Jeder Wechsel kann den Cache einer laufenden Sitzung kosten. Deshalb muss der Vorsprung mindestens ${fmtPct(th.rel_gain_min ?? 0.1)} betragen. Das gilt auch zurück, so dass der Router nicht zwischen zwei fast gleich teuren Anbietern pendelt.`,
        'Eine feste Wartezeit gibt es bewusst nicht: Wird ein Anbieter jetzt teuer, wird er sofort verlassen, wenn sich das rechnet.',
      ],
    },
    flank: {
      title: 'Preis-Flanke',
      short: 'Eine Preisänderung bei einem Anbieter, erkannt zwischen zwei Abfragen.',
      body: [
        `Der Router fragt alle ${th.refresh_min ?? 5} Minuten die Live-Preise ab (solange opencode benutzt wird) und vergleicht mit dem letzten Stand. Jede Änderung (Input, Output, Cache-Read, Verfügbarkeit, neuer oder verschwundener Anbieter) wird als Ereignis gespeichert.`,
        'Die Entscheidung wird bei jeder Abfrage neu berechnet. Sie ist deterministisch: gleiche Preise und gleiche Nutzung ergeben immer dieselbe Entscheidung.',
      ],
    },
    profile: {
      title: 'Nutzungsprofil',
      short: 'Die gemessenen Eigenschaften unseres Verbrauchs.',
      body: [
        'Cache-Quote, Output-Anteil und Kontextgröße je Anfrage werden aus der echten Nutzung gemessen. Der Router nimmt „heute“, wenn genug Daten da sind, sonst die letzte Woche, sonst Standardwerte.',
        'Mit der Cache-Quote ändert sich das Ranking: Bei hoher Quote zählt vor allem der Cache-Read-Preis, bei niedriger der Input-Preis.',
      ],
    },
    windows: {
      title: 'Heute, Woche, Standardwerte, Sitzungen',
      short: 'Dieselbe Rechnung mit unterschiedlichen Nutzungsprofilen.',
      body: [
        'Heute und Woche nutzen den tatsächlichen Verbrauch dieses Zeitraums. Ist ein Zeitraum zu dünn (wenige Anfragen), steht „zu wenig Daten“ dabei, und man sollte dem Ranking dort nicht trauen.',
        'Standardwerte sind angenommene Werte (90 % Cache-Quote), unabhängig von der Nutzung. Sitzungen zeigen, welcher Anbieter für den Verlauf einer einzelnen Sitzung am günstigsten gewesen wäre (anonym nummeriert).',
      ],
    },
    warm: {
      title: 'Wechselrechnung für laufende Sitzungen',
      short: 'Wann eine Sitzung mit heißem Cache ihren Anbieter verlässt.',
      body: [
        'Für jeden Anbieter: Vorsprung des besten Anbieters in Prozent, Kosten des Cache-Verlusts beim Wechsel und die Minuten aktiver Nutzung, bis sich der Wechsel eingespielt hat. „Wechselt“ heißt, beide Bedingungen sind erfüllt.',
      ],
    },
    whatif: {
      title: 'Entscheidungshilfe (Was wäre wenn)',
      short: 'Rechnet das Ranking mit eigenen Werten neu. Ändert nichts am Router.',
      body: [
        'Die Regler wirken nur in dieser Anzeige im Browser. Sie verändern weder den Router noch gemessene Werte. Nimm sie, um zu sehen, ab welcher Cache-Quote oder bei welchem Verbrauch ein anderer Anbieter günstiger wäre.',
      ],
    },
    picks: {
      title: 'Meine Top 3',
      short: 'Die drei günstigsten Modelle der Liste, die für Agenten taugen.',
      body: [
        'Aus den zehn beliebtesten Modellen werden nur bezahlte Modelle mit Tool-Unterstützung und mindestens 128 k Kontext betrachtet (kostenlose haben meist harte Limits und sind experimentell).',
        'Die Kosten werden mit unserem gemessenen Nutzungsprofil gerechnet (Cache-Quote und Output-Anteil aus dem Router). Hat ein Modell keinen Cache-Preis, wird der volle Input-Preis angesetzt.',
        'Das ist eine reine Preisrechnung. Qualität wird nicht bewertet; die Beliebtheit in den Top 10 ist nur ein Hinweis darauf.',
      ],
    },
    forecast: {
      title: 'Prognose',
      short: 'Nächste 24 Stunden aus dem Tagesverlauf der letzten Woche.',
      body: [
        'Für jede Stunde des Tages wird der Durchschnittsverbrauch der letzten Tage genommen und mit dem Preis des aktuellen, des besten und des nächstbesten Anbieters bewertet.',
        'Das ist eine Schätzung, keine Messung. Links vom Jetzt-Strich steht die tatsächliche Nutzung (mit demselben Preismodell bewertet), rechts die Prognose.',
      ],
    },
  };
  return T[key] || { title: key, short: '', body: [] };
}
