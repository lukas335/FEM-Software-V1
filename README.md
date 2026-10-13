# Pressure Sensor FEM

Kompakte Forschungsanwendung zur parametrischen Untersuchung einer Messhalbschale im Kontakt mit einer flüssigkeitsgefüllten Druckkugel, mit FEBio als externem Solver.

## Stand

Das PySide6-Grundgerüst startet. Der isolierte Solver-Prototyp erzeugt ein grobes parametrisches Gmsh-Wedge-Netz und rechnet Druckvorspannung, Neo-Hookean und reibungsfreien Kontakt erfolgreich mit FEBio 4.13.0. Die Kraft-Verschiebungs-Daten werden als CSV ausgegeben. Die wissenschaftliche Validierung steht noch aus. Die Arbeitsreihenfolge steht in [DEVELOPMENT.md](DEVELOPMENT.md).

Die Desktop-Oberfläche startet maximiert im Tab **Modell**. Die Geometrievorschau aktualisiert sich beim Ändern von Radien, Wanddicken, Anfangsspalt, Druck und Zustellweg. Sie zeigt den unbelasteten, maßstäblichen Axialschnitt, keine vorweggenommene Simulation.

**Simulation starten** übernimmt alle eingegebenen Werte einschließlich Materialien, Netzqualität und Wedge-Winkel. FEBio wird in der Standardinstallation, über `PATH` oder `FEBIO_EXE` gesucht; alternativ lässt es sich mit **Solver wählen …** auswählen. Netzgenerierung und Solver laufen in einem separaten Prozess, die Oberfläche bleibt bedienbar. Eingaben und Startbutton sind währenddessen gesperrt. Nach erfolgreichem Abschluss wechselt die App automatisch zu den neuen Ergebnissen inklusive Animation. Weitere Änderungen und Starts sind danach direkt möglich.

Jeder GUI-Lauf erhält einen eigenen Ordner `work/simulations/run_<Zeitstempel>_<ID>/` mit `parameters.json`, Eingabenetz, Solverprotokollen, `case.json`, Kurve und Verschiebungen. Alte Ergebnisse werden nicht überschrieben. Bei Fehlern bleiben sie angezeigt; Details stehen im Fehlerdialog und in `launcher.log`. Das Fenster kann während eines Laufs nicht geschlossen werden, damit keine verwaisten Solverprozesse entstehen; der Solver hat ein Zeitlimit von 900 Sekunden.

Im Tab **Ergebnisse** dominiert rechts die animierte Netzverformung mit Gesamtmodell und Kontakt-Detail. Abspielen/Pause, Zeitregler und Tempo steuern Druckaufbau und Indentation; die kompakte Kraftkurve links folgt dem aktuellen Zustand. Die Darstellung verwendet tatsächliche Knotenverschiebungen aus `nodal_displacements.csv`, standardmäßig im Maßstab 1×. Optional sind eine gekennzeichnete 3×/5×-Überhöhung und ein Referenznetz verfügbar. Zwischen gespeicherten Solverständen wird für die Wiedergabe linear interpoliert; die Lastzeit ist bei dieser quasistatischen Rechnung keine physikalische Dauer. Wiederholte Fußnoten und Erklärungstexte sind entfernt; technische Details stehen in Tooltips. Fehler, Kontaktvorlast und ungeeignete Fit-Bereiche bleiben sichtbar.

Links stehen Kraftkurve und Druckverlauf mit synchronen Animationsmarkierungen. Der Druckplot zeigt zunächst die Indentation, sodass auch kleine Druckanstiege sichtbar bleiben; **Druckaufbau mit anzeigen** blendet die gesamte Lastzeit ein. Die Lastzeit ist keine physikalische Zeit in Sekunden. Ohne Druckhistorie wird ausdrücklich kein Druckverlauf dargestellt. **Plot exportieren** exportiert beide Diagramme gemeinsam als PNG/SVG.

Der lineare Fit wird automatisch vom ersten belasteten Kurvenpunkt bis zum maximal tatsächlich gerechneten Zustellweg berechnet, ohne manuelle Fit-Felder. Kontakt gilt als erkannt, wenn drei aufeinanderfolgende Kräfte über `max(10⁻⁸ N, 0,1 % der maximalen Kraft)` liegen. Eine lokale Extrapolation der ersten zwei belasteten Punkte auf Kraft null schätzt den Kontaktweg innerhalb des vorherigen/ersten belasteten Zeitschritts. Diese Schätzung wird als Näherung markiert; Fit und Kennwerte verwenden ausschließlich reale belastete Punkte mit freiem Achsenabschnitt, ohne künstlichen Nullpunkt. Tooltip und Kurven-Hover zeigen den Weg relativ zum Kontakt. Bei bereits vorhandener Kontaktvorlast, fehlendem Kontakt oder zu wenigen Punkten gibt es einen Hinweis statt Fit-Kennwerten. Die Felder `fit_start_mm`/`fit_end_mm` bleiben nur zur Kompatibilität alter Parametersnapshots bestehen; für die automatische Auswertung werden sie ignoriert.

Beim Start wird der neueste erfolgreiche GUI-Lauf geladen; historische Baselines dienen als Rückfall. Weitere CSV-Dateien lassen sich über **Ergebnis öffnen** laden; **Aktualisieren** liest den angezeigten Lauf neu ein und berechnet den Fit erneut. Zoom und Verschieben sind über die Plot-Werkzeugleiste verfügbar.

Allein das Öffnen der App oder Ändern von Parametern startet **keine** Berechnung. Gespeicherte Ergebnisse bleiben unverändert, bis ein neuer Lauf abgeschlossen ist. Bei alten Läufen ohne Verschiebungsexport wird nur das Referenznetz gezeigt und die Wiedergabe deaktiviert. Ein SHA-256-Abgleich verhindert das Laden von Verschiebungen zu einer anderen Eingabedatei.

Nach erfolgreichem Ergebnisexport wird das Aufräumen großer Arbeitsdateien nur noch bestmöglich versucht. Gesperrte Dateien oder Ordner erzeugen eine **Aufräumwarnung**, aber keinen Solverfehler; Ergebnisse und Animation werden trotzdem geladen. `case.json` enthält `cleanup_warnings` und setzt `temporary_files_cleaned` nur bei vollständig erfolgreichem Aufräumen auf `true`. Echte Solver- oder Exportfehler bleiben weiterhin Fehler.

## Messschale: bisherige Definition oder Editor-Import

Unter **Modell → Messschale → Definition** bleiben **Wie bisher: Radius und Wanddicken** und **Importierte Editor-Geometrie** getrennt auswählbar. Beim Umschalten bleiben die bisherigen Zahlen und der geladene Import erhalten.

Im Editor zuerst Bereiche, Materialien, Interfaces und Randverhalten zuweisen und die gesamte Skizze als JSON **speichern**. Im Hauptprogramm **Messschale importieren …** wählen. Jeder Bereich wird als eigener Materialkörper zum 3D-Wedge rotiert. Verklebt bedeutet gemeinsame konforme Netzknoten; reibungsfrei bedeutet getrennte Knoten mit reibungslosem FEBio-Kontakt. Äußere Konturflächen sind mögliche Kontaktflächen zur Druckkugel beziehungsweise Deckschale.

**Kontakt-Referenz** bestimmt den axialen Nullpunkt für den Anfangsspalt. **Messpunkt** bestimmt getrennt den Punkt und Materialbereich für die Wegauswertung (Zustellweg minus dessen axiale Bewegung). Gerade bei gleitenden Materialbereichen beide Zuordnungen kontrollieren. Vorgeschlagen werden der vorderste äußere Achsenpunkt als Kontakt-Referenz und der nächste äußere Achsenpunkt als Messpunkt; beide sind manuell änderbar.

Nur Kanten mit **Vorgegebene Verschiebung** erhalten eine axiale Zustellvorgabe. Auf der Rotationsachse werden die zugehörigen Knoten statt einer nicht vorhandenen Rotationsfläche verwendet. Andere Kanten bleiben ohne zusätzliche Verschiebungsvorgabe; Rotationsachsen- und Wedge-Symmetriebedingungen gelten weiter. Ohne ausgewählte Zustellkante wird kein Lauf gestartet. Materialien mit noch nicht definierter Faserorientierung sowie widersprüchliche Interface-Kombinationen werden ausdrücklich abgewiesen.

Die vollständige importierte Definition wird in GUI-Einstellungen und Lauf-Parametersnapshots eingebettet. Eine spätere Änderung oder Entfernung der Quelldatei verändert keinen gespeicherten Lauf; neue Editor-Änderungen müssen erneut importiert werden. Vorschau und Ergebnisanimation zeigen die Materialbereiche getrennt. Die R/tx/ty-Rasterstudie bleibt dem bisherigen parametrischen Modus vorbehalten; importierte Geometrien werden zunächst als Einzelrechnungen verwendet.

## Netzprüfung vor dem Solverstart

Im Modell-Tab erzeugt **Netz & Randbedingungen prüfen** das tatsächliche FEBio-Eingabenetz in einem eigenen Ordner unter `work/mesh_previews/`, ohne FEBio zu starten. Der Tab **Netz & Randbedingungen** zeigt den unverformten Axialschnitt mit Materialbereichen, Zustellknoten, Fixierungen, Druck- und Kontaktflächen. Die Tabelle enthält die tatsächlich exportierten Bedingungen einschließlich des jeweiligen Lastschritts; eine ausgewählte Zeile wird gelb hervorgehoben. Symmetrie- und Kontaktflächen lassen sich zusätzlich einblenden. Die Darstellung ist nur eine Prüfung, keine Änderung der Bedingungen.

**Letztes Netz öffnen** bleibt auch während eines Laufs verfügbar. Bei neuen Simulationen erscheint das erzeugte Netz bereits vor dem Solverstart in diesem Tab; das Protokoll wird regelmäßig aktualisiert. Bereiche ohne axiale Vorgabe und ohne gemeinsame Knoten mit axial gehaltenen Körpern erhalten einen Hinweis auf mögliche Starrkörper-Moden. Das ist keine vollständige Stabilitätsanalyse: reibungsfreier Kontakt kann je nach Kontaktzustand tragen oder offen sein. Die Prüfung fügt keine versteckten Lagerungen hinzu.

## Parameterstudie

Der neue Tab **Parameterstudie** hat eine eigene editierbare Konfiguration. **Modellparameter übernehmen** kopiert Kugelgeometrie, Materialien, Spalt, Hub, Netz und Druckmodus aus dem Modell-Tab. Radien-/Dicken-/Drucklisten mit Semikolon oder Leerzeichen trennen; Dezimalkommas sind erlaubt. Referenzgeometrien können frei hinzugefügt, geändert oder entfernt werden. „Ohne Innendruck“ erzeugt pro Geometrie nur einen Fall ohne Volumenbindung; dafür gibt es keine Druckempfindlichkeit.

**Referenzen prüfen** und **Studie starten** sind getrennte, explizite Starts. Nach einer Referenzprüfung beginnt kein Raster automatisch. Identische Referenz-/Rasterfälle werden nur einmal gerechnet. Nach Start ist die Konfiguration eingefroren; Änderungen über **Neue Studie / Kopie** erzeugen später einen separaten Studienordner. Einzelrun und Studie können nicht gleichzeitig laufen. **Nach aktuellem Lauf stoppen** und **Fortsetzen** sichern abgeschlossene Fälle. Solverfehler werden nur mit gesetztem Haken erneut versucht; ungültige Fits erfordern eine passende neue Konfiguration.

Neue Studien verwenden **Regression → Automatisch ab Kontakt**. Kontakt wird für jeden Lauf erkannt; aus den belasteten Kurven wird automatisch ein gemeinsamer Wegbereich nach Kontakt gebildet: Beginn = spätester erster belasteter Punkt relativ zum jeweiligen Kontakt, Ende = kleinster erreichter Kontaktweg. Damit wird kein Leerweg und kein künstlicher Nullpunkt gefittet. Nach jedem neuen gültigen Lauf werden die bisherigen Fits auf denselben Bereich aktualisiert. Bereich und Rangliste sind als vorläufig markiert, solange Fälle ausstehen. Die endgültige Auswahl ist unabhängig von der Reihenfolge der Läufe. Fehlende Kontakte und fehlgeschlagene Solverläufe bleiben ausdrücklich ungültig/fehlend.

**Manueller Bereich nach Kontakt** bleibt optional; nur dann erscheinen „Fitbeginn nach Kontakt“ und „Fitende nach Kontakt“. Der Fit verwendet mindestens fünf originale Solverpunkte und eine über den Weg integrierte Regression der stückweise linearen Kraftkurve. R² und RMSE sind konsistent weggewichtet. Druckvorlast, unvollständige Kontaktprotokolle, fehlender Kontakt, zu kurzer Hub und zu wenige Punkte verhindern einen gültigen Fit. Ein manuelles Fitende von 4 mm nach Kontakt ist mit 4 mm Gesamtzustellweg und vorhandenem Leerweg nicht erreichbar; dafür ist Hubreserve erforderlich. Die Lastkurve wird nie extrapoliert. Ein manuell gewählter Bereich wird nicht automatisch verkürzt.

Bestehende Studien behalten ihre ursprüngliche manuelle Auswertung. **Studie öffnen → Automatisch neu auswerten** erzeugt eine separate automatische Auswertungskopie aus den vorhandenen, validierten Solverdateien; kein FEBio-Start und keine Änderung der Originalstudie. Bislang wegen manueller Fitgrenzen ungültige Fälle werden dabei erneut ausgewertet. Nicht gerechnete Fälle bleiben ausstehend; die Kopie kann später explizit fortgesetzt werden. Große FEBio-Binärplots werden nicht kopiert, die CSV-basierte Animation bleibt verfügbar.

Die Auswertung zeigt eine sortierbare Rangliste, R/t-Farbkarte und Kraft-/Steifigkeitskurven. Grau/„—“ bedeutet fehlend oder ungültig, „!“ weist auf nicht bevorzugte Kandidaten hin. Einzelheiten stehen in der Falltabelle; Doppelklick öffnet vorhandene Kurven in der Einzelansicht (deren automatischer Gesamtfit ist vom gemeinsamen Studienfit unabhängig). Primärkennwert ist die Steifigkeitsänderung pro mmHg zwischen niedrigstem und höchstem Druck. Relative und benachbarte Druckempfindlichkeiten stehen zusätzlich in `ranking.csv`; einheitliche Modi werden nicht vermischt.

Unter `work/studies/<ID>/` liegen `study.json`, `cases.json`, `results.csv`, `ranking.csv` und eigene Laufversuche in `runs/`. Materialänderungen seit Studienbeginn verhindern ein vermischtes Fortsetzen. Versionierte **`checkpoints/state_*.json` sind der maßgebliche Studienstand**. Windows-Dateisperren werden kurzzeitig erneut versucht; gesperrte Spiegeldateien/CSV-Exporte stoppen nicht den gesicherten Lauf. Beim Wiederöffnen wird der neueste Checkpoint gelesen. Fertige Solverläufe, deren Status beim Speichern verloren ging, werden anhand ihrer Parameter und Ergebnisdateien wiedererkannt. Eine Prozesssperre verhindert gleichzeitiges Rechnen derselben Studie in zwei aktuellen App-Instanzen. Fehlende Schreibrechte auf den eigentlichen Checkpoint stoppen weiterhin sicher. Alte Dateien und gescheiterte temporäre Dateien werden nicht automatisch gelöscht.

Sieben gezielte Tests für Kombinationen, automatische gemeinsame Fitgrenzen, weggewichteten Fit, Rangfolge, Dateisperren, Ergebniswiederherstellung/Auswertungskopie und Stop/Fortsetzen: `python -m unittest discover -s tests -p test_study.py -v`. Diese Tests starten weder Gmsh noch FEBio. Die Oberfläche wurde zusätzlich mit vorhandenen Studiendaten geprüft. Eine vollständige Studie wurde vom Agenten nicht gestartet; echte Läufe bleiben nutzergesteuert.

## Installation

```text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Für den isolierten Solver-Prototyp:

```text
python -m solver.prototype --generate-only
python -m solver.prototype --febio "C:\Program Files\FEBioStudio\bin\febio4.exe"
python -m solver.prototype --febio "C:\Program Files\FEBioStudio\bin\febio4.exe" --initial-gap-mm 1.0 --output work/simulations/baseline_gap1mm
python -m unittest discover -s tests -p test_prototype.py
```

Die generierten Dateien liegen unter `work/simulations/baseline_prototype/`. Der erste Befehl benötigt Gmsh, der zweite zusätzlich FEBio 4. Ein erfolgreicher Solverlauf schreibt `force_displacement.csv`; die Kraft ist von 3° auf 360° hochskaliert. `solver.log`, `stdout.txt`, `stderr.txt` und `case.json` dokumentieren den Lauf.

## Geschlossene Druckkugel

Unter **Belastung → Druck konstant halten** kann zwischen zwei Modellen umgeschaltet werden. **Aus** (Standard): geschlossenes Flüssigkeitsvolumen, Druck verändert sich bei Indentation. **An**: Druckaufbau auf P₀, anschließend konstanter vorgegebener Druck; das Innenvolumen darf sich ändern, entsprechend einem druckgeregelten Reservoir. Geometrie, hintere Einspannung und Material bleiben gleich. Die Auswahl gilt für den nächsten gestarteten Lauf und verändert keine bereits geladenen Ergebnisse. Ergebnisüberschrift und Parametersnapshot dokumentieren den tatsächlich gerechneten Modus (`constant_pressure`).

Auch bei konstantem Druck wird `cavity_history.csv` geschrieben: Druck entsprechend der vorgeschriebenen Lastkurve und Volumen aus der tatsächlich verformten Innenfläche. Die aus Kompatibilitätsgründen unveränderte Spalte `relative_volume_error` beschreibt dann eine **zulässige relative Volumenänderung**, keinen Fehler; der Tooltip benennt sie entsprechend. Die Volumentoleranz gilt ausschließlich im geschlossenen Modus. Historische Parametersnapshots ohne Schalter verwenden weiterhin das geschlossene Modell.

Die rechte Wand ist eine vollständige Hohlkugel. Alle Wandknoten der hinteren Hälfte (`x ≥ Kugelmittelpunkt`) sind in allen drei Verschiebungsrichtungen fest eingespannt, einschließlich der inneren Wand. Die feste Hälfte ist in Vorschau und Netzanimation grau markiert. Die eingegebenen Radien beschreiben die unbelastete Geometrie.

1. Druckaufbau: Aufbringen des Anfangsüberdrucks **P₀** bei ruhender Messhalterung.
2. Indentation: Das **nach dem Druckaufbau erreichte Innenvolumen** wird durch FEBios `volume`-Constraint festgehalten. Der zusätzliche Reaktionsdruck ergibt sich aus der Verformung; der Gesamtdruck ist P₀ plus Reaktionsdruck. Ein Fluidnetz ist für diese quasistatische, räumlich gleichförmige Druckannahme nicht erforderlich.

Die Volumenbedingung wird erst in Phase 2 aktiviert und per Augmented Lagrangian bis auf numerische Toleranz erfüllt. Die Kavitätsnormalen zeigen aus dem Fluid nach außen; deshalb verwendet die FEBio-Konvention hier einen negativen Penalty-Faktor. Die Wedge-Schnittflächen liegen auf Symmetrieebenen durch die x-Achse und tragen zum verwendeten Oberflächen-Volumenintegral nicht bei. Kraft und Volumen werden auf 360° skaliert, Druck nicht.

Die Animation zeigt den aktuellen Innendruck. Der Tooltip enthält Innenvolumen und während der Indentation den relativen Volumenfehler; `cavity_history.csv` enthält die gesamte Druck-/Volumenhistorie mit Bindung an das Eingabenetz. Im geschlossenen Modus werden neue Läufe bei mehr als 0,01 % relativer Volumenabweichung als fehlerhaft verworfen.

Ein gezielter Lauf mit Sklera-Preset, P₀ = 15 mmHg, 1 mm Spalt und 1 mm Zustellweg (`run_20261003_001240_eb698693`) konvergierte mit 790 Knoten und 1742 Tet4-Elementen. Enddruck: **15,1061 mmHg**, Endkraft: **0,02936 N**, maximale relative Volumenabweichung: **0,0000025 %**. Alle 346 Knoten der hinteren Hälfte bleiben unbewegt. Dies ist ein technischer Integrationsnachweis, keine Material- oder Netzvalidierung. Die starre hintere Hälfte ist eine bewusst vereinfachte Lagerung und beeinflusst die Druckantwort stark.

## Historische Halbschalen-Baseline

R1 = 8 mm, t1 = 1.25 mm, R2 = 22 mm, t2 = 1 mm, Silicone A30, 15 mmHg, Wedge 3°, u_max = 1 mm.

Der Prototyp verwendet jetzt **1,0 mm Anfangsspalt** als zentralen Modellparameter `initial_gap_mm` (CLI: `--initial-gap-mm`). Netzgeometrie und FEBio-Flächennormalen verwenden denselben Parameter; er wird in `case.json` dokumentiert und in der GUI angezeigt. Der alte Lauf mit 0,1 mm Spalt bleibt erhalten, ist aber wegen unerwünschten Kontakts bereits während des Druckaufbaus kein geeigneter Ausgangszustand für die Messung.

Der korrigierte Lauf `baseline_gap1mm` endet normal, mit 453 Knoten, 983 Tet4-Elementen und 34 Animationsständen inklusive Referenz. Bei 15 mmHg beträgt die freie axiale Ausdehnung etwa 0,385 mm, der verbleibende Spalt etwa 0,615 mm. Während der gesamten Druckphase sind die ausgegebenen Kontaktdrücke null; die Halterungsreaktion am Ende ist numerisch null. Der erste gespeicherte belastete Zustand liegt bei 0,65 mm Zustellweg, die Endkraft bei 1 mm beträgt 0,04086 N.

Der Zustellweg enthält den Leerweg, ist also **nicht** die Eindrückung ab Erstkontakt. Der Baseline-Fit liegt deshalb bei 0,75–1,0 mm Zustellweg. Für andere Drücke, Materialien und Geometrien müssen freier Druckaufbau, Restspalt, verfügbarer Eindrückweg und Fit-Bereich erneut geprüft werden; 1 mm ist keine universelle Abstandsgarantie. Diese Werte sind technische Referenzen für unkalibrierte Prototypmaterialien.

## Sklera-Druckschale

Im Modell-Tab ist für die Druckschale jetzt **Sklera (unkalibriert)** voreingestellt; die Messschale bleibt Silicone A30. Die beiden Silikonmaterialien bleiben für die Druckschale auswählbar. Nach Auswahl **Simulation starten** verwenden: Vorhandene Silikon-Ergebnisdateien werden nicht nachträglich verändert. Die Ergebnisüberschrift zeigt das tatsächlich gerechnete Druckschalenmaterial.

Verwendet wird FEBios [Mooney-Rivlin von Mises Fibers](https://febiosoftware.github.io/febio-docs/features/features/solid_material_mooney-rivlin_von_mises_fibers/). Pro Druckschalen-Element wird eine orthonormale tangentiale Faserebene am Elementschwerpunkt zugewiesen. Die Fasern sind zunächst gleichmäßig in dieser Ebene verteilt (`vmc=1`, `kf=0`), ohne bevorzugte Richtung. Diese vereinfachte Faserarchitektur ist mit der Wedge-Symmetrie vereinbar; sie bildet keine regional gemessene Kollagenarchitektur ab.

Die zentralen Startwerte in `model/materials.py` sind **illustrative Modellannahmen, keine Literaturkalibrierung**: `c1=0,15 MPa`, `c2=0`, `c3=0,05 MPa`, `c4=30`, `lam_max=1,1`, `K=30 MPa`, `tp=0°`, `gipt=40`. `c5 ≈ 30,1283 MPa` wird aus der Steigung des exponentiellen Zweigs am Übergang berechnet. Die Materialdefinitionen werden zusätzlich zu den Eingabeparametern in `case.json` gespeichert; die tatsächlich verwendeten FEBio-Koeffizienten stehen in `model.feb`.

Das Preset ist keine validierte menschliche oder tierische Sklera. Es berücksichtigt weder regionale Eigenschaften noch Viskoelastizität. Insbesondere muss das grobe Tet4-Netz bei der höheren Volumensteifigkeit auf künstliche Versteifung (Locking) geprüft werden. Geometrie, Hub und Fit-Bereich bleiben beim Materialwechsel unverändert und müssen zur jeweiligen Anwendung passen.

## Einschränkungen

Die Materialwerte sind unkalibrierte Prototypwerte und nicht für wissenschaftliche Aussagen geeignet. Das grobe Tet-Netz dient zunächst allein dem Solver-Smoke-Test; Netzkonvergenz, Kontaktverhalten und Belastbarkeit der Resultate sind noch zu prüfen. Druckvergleich und Parameterstudien sind noch nicht implementiert.

## Tests

`python -m unittest discover -s tests -v` prüft Parameter, Netz, Ergebnisreader und GUI-Verhalten. Der gespeicherte korrigierte Baseline-Lauf wird bei Vorhandensein zusätzlich geprüft. Für zwei echte GUI→FEBio→Ergebnis-Rundläufe in PowerShell vorher `$env:FEM_RUN_SOLVER_TESTS='1'` setzen. Bei lokaler Gmsh-Installation in `.vendor` zusätzlich `$env:PYTHONPATH='.vendor'`. Die Integrationstests erzeugen eigene Laufordner und prüfen geänderte Parameter, eine weiterhin reagierende Qt-Ereignisschleife und das automatische Laden von Kurve und Animation.
