# Entwicklungsreihenfolge

## Studien, Tangentialkontur und hintere Netzvergröberung (04.10.2026)

- Modellübernahme unterstützt R/tx/ty; gekoppelte historische Dickenlisten bleiben
  beim Kopieren gekoppelt (Checkbox). Materialvergleich ignoriert nur Beschreibungen,
  nicht Koeffizienten; JSON-Listen und Tupel werden gleich behandelt.
- Ergebnistabelle zeigt relative Änderung in Prozent zwischen niedrigstem und höchstem
  Studiendruck, absteigend sortiert. Farbmatrix entfernt; Kurvendetails bleiben verfügbar.
- Bei ty>tx: Innenradius R-ty, Mittelpunktversatz ty-tx, Kreis bis 90° und tangentiale
  Gerade zur Öffnungsebene. Vorschau und CAD verwenden dieselbe Definition.
- Hintere Deckschale: Zielgröße Faktor 3 mit 20° Übergang; nur hintere CAD-Entitäten,
  verklebte Grenzfläche unverändert fein. Isotrope Tet4-Vergröberung garantiert keine
  zwei Elementlagen über die Dicke. Numerische Auswirkungen noch zu prüfen.
- Geometrie-/Netzversion schützt betroffene alte Studien vor gemischtem Fortsetzen.
  Keine Solverläufe zur numerischen Verifikation durchgeführt.

## Messschale R/tx/ty – Einzel-Solverpfad

- `geometry/measurement_mesh.py`: exakte OCC-Kreisbögen mit getrennten Mittelpunkten,
  unveränderter Öffnungsebene und Außenposition; lokale Netzgröße berücksichtigt tx und ty.
- Netzgenerator nutzt dieselbe Geometriedefinition wie die Vorschau. Innere
  Flächennormalen im FEBio-Writer beziehen sich auf den exzentrischen Innenmittelpunkt.
- Einzelstarts über GUI/CLI freigeschaltet; Kontaktpaare, Fluidvolumen, Zustellung
  und Achsfixierungen unverändert. Neue Läufe tragen `eccentric_inner_sphere_v1` als
  separate Messschalen-Geometrieversion. Studien bleiben vorerst konzentrisch.
- Keine Tests oder Solverläufe ausgeführt; numerische Prüfung noch ausstehend.

## Messschale R/tx/ty – Schritt 1: Vorschau

- Eigenes `MeasurementShellParameters`; alte Dicken werden beim Einlesen nach
  tx=ty migriert, widersprüchliche Mischformate abgelehnt.
- `geometry/measurement_shell.py` definiert den exzentrischen Innenkreis bei
  unverändertem Außenkreis und unveränderter Öffnungsebene. GUI zeigt beide Dicken.
- Exzentrische Solverstarts vorerst gesperrt (Runner, Netzgenerator, FEBio-Writer,
  Studien). Konzentrische Läufe über expliziten Dickenadapter weiterhin angebunden.
- Studien bleiben in diesem Schritt konzentrisch; historische IDs erhalten.
- Noch ausstehend: Schritt 2, exzentrischer CAD-/Netzpfad, innere Flächennormalen
  und dreiparametrige Studien einschließlich Referenzen, Ranking und Farbkarte.
- Keine Tests oder Solverläufe ausgeführt; zunächst visuelle Abnahme durch Nutzer.

## FEM: vollständige Deckschale (03.10.2026)

- Deckschale jetzt im Netz-/Solverpfad aktiv: vorne exzentrische Außenkontur aus tx/ty,
  hinten konstantes ty. Materialtrennung radial bei `free_cap_angle_deg` (Standard 70°).
  Nur die beiden Deckschalenvolumina werden fragmentiert; gemeinsame Grenzknoten bilden
  die Verklebung. Druckkugel und Deckschale behalten getrennte Kontaktknoten.
- Zwei reibungsfreie Kontaktpaare: Messschale–Deckschale außen und Druckkugel–Deckschale
  innen. Nur Deckschalen-Außenfläche ab dem Grenzwinkel vollständig eingespannt;
  Druckkugel ohne hintere Einspannung. Achsknoten bleiben exakt quer fixiert.
- Fluidvolumen weiterhin ausschließlich innerhalb der Druckkugel; Konstantdruckoption
  und Zielkraftsteuerung erhalten. Cavity-Logger-ID aus dem Input gelesen statt fest 5.
- Neue unkalibrierte MR-Karten `lid_demo` und `orbital_demo`; c1/c2/K separat je
  Deckschalengebiet im GUI editierbar (MPa). Startwerte sind rein illustrativ, keine
  Gewebekalibrierung; keine Viskoelastizität. Tatsächliche Karten in Lauf-/Studiensnapshots.
- Studien übernehmen die Deckschale aus dem Modell-Tab. Beide Materialgebiete in der
  Animation sichtbar; Einspannungsmarkierung auf Deckschale. Alte Zweikörperläufe bleiben lesbar.
- Anfangsspalt bezieht sich weiterhin auf die nackte Druckkugel: `gap > tx` plus
  Reserve für Inflation erforderlich. Kontaktfreiheit zur Messschale nach Druckaufbau
  wird auch bei neuen weggesteuerten Deckschalenläufen anhand der Knoten geprüft.
- Auf Nutzerwunsch KEINE Tests, GUI-Starts oder Solverläufe ausgeführt. Numerische
  Konvergenz noch offen: Kontaktinitialisierung bei nicht passenden Oberflächennetzen,
  mögliche freie Modi ohne aktive Kontakte, Tet4-Locking bei großem K/μ sowie hohe
  Verformung sind im ersten Nutzerlauf zu beurteilen. Keine versteckte Ersatzfixierung.
- CAD-API: [Gmsh OCC-Dokumentation](https://gmsh.info/doc/texinfo/gmsh.html#Namespace-gmsh_002fmodel_002focc).

## Vorheriger Stand: vollständige Deckschale mit zwei Materialgebieten (GUI-Vorschau)

- Vorne exzentrische Außenkontur aus tx/ty, hinten konzentrische Fortsetzung mit ty.
  Radiale Materialgrenze um den Druckkugelmittelpunkt bei einstellbaren 70° vom vorderen Pol.
- Separate Materialien vorne/hinten; alte Deckschalen-Snapshots übernehmen ihr Material für beide Gebiete.
- Vorschau markiert die verklebte Gebietsgrenze und ausschließlich die hintere äußere
  Deckschalenkontur als eingespannt. Druckkugel bei aktiver Deckschale ohne Einspannungsmarkierung,
  dafür reibungsfreier Innenkontakt. Ohne Deckschale bleibt die bisherige Darstellung erhalten.
- Nur geplante Geometrie/Randbedingungen: Solverstart bleibt gesperrt; Netz und Solver unverändert.
  Wie vereinbart ohne Tests oder Solverlauf umgesetzt, noch ungeprüft.

## Deckhalbschale: reine Geometrievorschau (03.10.2026)

- Optional im Modell-Tab, mit Pol-Dicke tx, Äquator-Dicke ty und eigenem Material.
- Innenfläche identisch zur linken Druckkugel-Außenfläche; Außenkugel aus tx/ty
  berechnet (`geometry/cover_shell.py`). Beide Flächen enden an der Druckkugel-Äquatorebene.
  Der Außenmittelpunkt darf nach links oder rechts versetzt sein.
- Vorschau mit Bemaßung, Mittelpunkten und Berührungs-/Überlappungshinweis;
  Messschale wird nicht automatisch verschoben. Alte Studien-IDs bleiben erhalten.
- Aktivierte Deckhalbschale wird nicht an Solver oder Studien übergeben.
  Keine Vernetzung, Kontakt- oder Solverintegration. Auf Wunsch ohne Tests und
  ohne Solverlauf umgesetzt; Vorschauerweiterung noch ungeprüft.

## Exakte Querfixierung der Achsknoten (03.10.2026)

- Der FEBio-Writer erzeugt `axis_transverse_fixed` für ursprüngliche Achsknoten
  mit `sqrt(y² + z²) < 1e-8 mm`, ohne bereits vollständig fixierte `pressure_support`-Knoten.
- Globale Randbedingung: `uy = uz = 0`; die axiale Bewegung bleibt frei.
  Die vier Symmetrie-Constraints und die Reihenfolge für den Kavitätslogger bleiben unverändert.
- Gilt nur für neu erzeugte Modelle; gespeicherte Ergebnisse und Darstellung bleiben unverändert.
- Auf ausdrücklichen Wunsch ohne Tests oder Solverlauf umgesetzt; noch nicht numerisch geprüft.

## Adaptive Zielkraftsteuerung (03.10.2026)

- Auswahl in Modell und Studie: fester Zustellweg oder **Zustellen bis Zielkraft** (360°-Kraft in N).
  Der maximale Zustellweg bleibt eine harte Grenze. Toleranzen und Schrittweiten stehen unter Regleroptionen.
- `solver/force_controller.py`: beschränkte Annäherung, gedämpfte Sekantenschritte, Halbierung nach
  Überschwingen oder Solverfehler; keine physikalische Entlastung vom überschossenen Zustand.
- `solver/febio_session.py`: Druckaufbau einmalig; fortgesetzter aktiver Indentationsschritt mit binären
  Checkpoints. Jeder Versuch besitzt eigenes Verzeichnis und eine Kopie seines Ausgangszustands.
  Die Druck- und Volumenbindung bleibt unverändert aktiv; kein erneutes Aufpumpen pro Versuch.
- `solver/accepted_history.py` / `target_force.py`: ausschließlich akzeptierter Ast in Kraft-, Druck-
  und Netzhistorie. `control_history.csv` dokumentiert auch verworfene Versuche; `accepted_path.json`
  dokumentiert Elternzustände, Zeiten und SHA-256 der Restart-Eingaben/Archive.
  Lastzeit ist eine Solver-Pseudozeit, keine reale Belastungsgeschwindigkeit.
- FEBio 4.13 beendet den gezielten Breakpoint nach `quit` mit Exit 1. Dieser wird nur zusammen mit
  Breakpoint-Meldung, bestätigtem letzten Dump und vollständigem konvergiertem Endzustand akzeptiert.
  Das Zeitlimit gilt kumulativ für die Solver-Session, nicht erneut je Versuch.
- Bekannte FEBio-4.13-Einschränkung: Der `face_data`-Kontaktlogger verliert beim Cold-Restart seine
  Oberflächenzuordnung und verursacht einen Access-Violation-Absturz. Deshalb nur im Zielkraftmodus
  kein solcher Textlogger. Kontaktfreiheit beim Druckaufbau wird stattdessen konservativ durch eine
  trennende Axialebene zwischen den tatsächlich verformten linearen Kontaktflächen geprüft.
  Ohne diesen Nachweis wird abgebrochen. Kraft-, Knoten- und Kavitätslogger bleiben erhalten.
- Teilresultate (z. B. Weglimit, Versuchslimit, Zeitlimit) werden ausdrücklich als **Zielkraft nicht
  erreicht** angezeigt und nicht in Zielkraft-Studien gerankt. Fit/Kontakterkennung bleiben unabhängig.
  Alte Parametersnapshots und Studien behalten Wegsteuerung und ursprüngliche Fall-IDs.
- Kleine reale Nachweise, keine volle Studie: Restart-Verzweigung unter `work/probes/restart_verified`;
  geschlossenes Volumen: Ziel 0,02 N, erreicht 0,0199419 N bei 0,939980 mm, maximaler relativer
  Volumenfehler 2,14e-8 (`work/probes/target_force_closed_02`). Konstantdruck: Ziel 0,005 N,
  erreicht 0,00494339 N bei 0,834947 mm; zwei Überschwingversuche ausgeschlossen, Druck 15 mmHg
  (`work/simulations/run_20261003_153008_62239d67`). Kein wissenschaftlicher Validierungsnachweis.
- Fokussierte Tests: `python -m unittest discover -s tests -p test_force_control.py -v` sowie
  `test_study.py`; echter zusätzlicher Solverlauf nur mit `FEM_RUN_TARGET_FORCE_TESTS=1`.
  Restart-Mechanik abgeglichen mit dem offiziellen [FEBio-Restart-Import](https://github.com/febiosoftware/FEBio/blob/develop/FEBioXML/FERestartImport.cpp)
  und der [Volumenconstraint-Serialisierung](https://github.com/febiosoftware/FEBio/blob/develop/FEBioMech/FEVolumeConstraint.cpp).

## Ursprüngliche Reihenfolge

Der technische Risikokern wird vor dem Ausbau der Oberfläche validiert.

1. **Solver-Prototyp:** ein einzelner Baseline-Fall mit grobem parametrischem 3°-Wedge-Netz.
2. **Physiktest:** Neo-Hookean-Material, Druckvorspannung und reibungsfreier finite-sliding Kontakt müssen in FEBio erfolgreich konvergieren.
3. **Text-Export:** Verschiebung und Reaktionskraft als robuste Ergebnisdatei auslesen.
4. **Erst danach:** GUI für Mesh-Generierung, Einzelruns, Druckvergleich und Sweep ausbauen.

Damit wird früh geklärt, ob die Modellierungsstrategie in FEBio tragfähig ist. GUI-Komfort, Heatmaps und Caching sind nachgelagerte Anwendungsentwicklung und dürfen diesen Nachweis nicht verdecken.

## Aktueller Stand: geschlossene, hinten fixierte Druckkugel

- Vollständige Hohlkugel im Wedge; sämtliche Wandknoten mit x ≥ Mittelpunkt sind in x/y/z fest. Tangentiale Sklera-Faserebenen werden auch hinten erzeugt.
- Phase 1: P₀ aufbauen. Phase 2: FEBio-Volumenconstraint erfasst beim Aktivieren das aufgepumpte Volumen; P₀ bleibt als Basislast, der zusätzliche Reaktionsdruck ist unbekannt. Kein explizites Fluidnetz.
- `cavity_history.csv`: Gesamtdruck in mmHg, Vollkugel-Innenvolumen in mm³ und relativer Volumenfehler. Unabhängige Integration der verformten Innenfläche wird mit FEBios Volumenausgabe verglichen. Abweichung vom gefüllten Zielvolumen > 0,01 % verhindert Erfolgsmeldung.
- Genau ein echter GUI→FEBio→Ergebnis-Lauf: `run_20261003_001240_eb698693`, FEBio 4.13.0, 790 Knoten, 1742 Tet4, 55 Zustände inklusive Referenz. Anfangsdruck 15 mmHg, Enddruck 15,106110 mmHg, Endkraft 0,0293602 N; maximale relative Volumenabweichung 2,495e−8. Keine Kontaktvorlast am Ende des Druckaufbaus (Reaktion rund 1,3e−13 N).
- Anschließend nur vorhandene Ergebnisse und GUI geprüft, kein weiterer Solverlauf: alle 346 hinteren Knoten bleiben unverändert, Volumenfehler innerhalb Toleranz, Druck steigt. Vorschau und Animation bei 1680×1000 visuell geprüft; feste Hälfte grau, aktueller Druck in der Animation. Neuester erfolgreicher GUI-Lauf wird beim Start geladen, ohne Solverstart.
- Bestehende Test-Erwartungen für Geometrie und den optionalen Sklera-Smoke-Test auf das neue Modell angepasst. Keine gesamte Testsuite ausgeführt. Netzkonvergenz, Materialkalibrierung und Sensitivität der stark vereinfachten Lagerung bleiben offen.

## Prototyp-Abnahmekriterien

Automatischer Studienfit ergänzt: `fit_mode=automatic` für neue Konfigurationen, `manual` als Migration für alte Snapshots. Beginn/Ende aus der Schnittmenge der gemessenen Kontaktbereiche aller geeigneten Fälle; bei neuem Ergebnis gemeinsame Neuberechnung ohne Solver. Vorläufige Bereiche/Ranglisten sind gekennzeichnet. Optionale manuelle Felder im Automatikmodus verborgen. Separate Neuauswertungskopie vorhandener Studien möglich, inklusive erneuter Prüfung bisher durch manuelle Fitgrenzen ungültiger Fälle. Sieben fokussierte Tests ohne Solver bestanden, GUI-Moduswechsel und Altstudienanzeige geprüft.

Parameterstudie implementiert: immutable Konfiguration mit freien Referenzen, Kugel-/Material-/Lastparametern und drei Druckmodi; sequenzielle, getrennt startbare Phasen mit Stop/Fortsetzen, Material- und Ergebnisbindung. Weggewichteter Fit ist das exakte Least-Squares-Integral der stückweise linearen Kraftkurve über einen gemeinsamen kontaktbezogenen Bereich. Rangliste, Farbkarte, Kurvendetails und CSV-Exporte integriert. Sechs fokussierte Tests ohne Solver bestanden, GUI mit bestehender Nutzerstudie geprüft. Dateisperren beim Windows-Replace führten während der ersten Nutzung zum Abbruch: Retry ergänzt, versioniertes Checkpoint-Journal als maßgebliche Quelle eingeführt, Spiegel-/CSV-Sperren sind nun nicht fatal. Fertige Läufe werden nach fehlgeschlagenem Checkpoint wiederhergestellt; Speicherfehler vor Solverstart werden nicht als Solverfehler verbucht. Studie `study_20261003_010058_12bf334e` nur gelesen: 12 ungültige Fits wegen δ-Ende=4 mm bei Gesamtweg=4 mm, 15 ausstehende Fälle nach Statusbereinigung im Speicher; keine Nutzerdaten verändert, kein neuer Solverlauf gestartet.

Optionaler Konstantdruck-Modus: Checkbox unter Belastung, Default aus, mit Snapshot-Feld `constant_pressure`. Bei Aktivierung entfallen Volumenconstraint und dessen Log-Ausgabe; die Drucklast bleibt nach dem Aufpumpen auf P₀. Volumen wird weiter aus dem verformten Netz integriert, Änderungen sind erlaubt und werden im Tooltip nicht als Fehler bezeichnet. Ein echter GUI→FEBio-Lauf bestanden: `run_20261003_003729_215fc235`, P(end) = 15 mmHg, ΔV/V = −8,89835e−5. XML ohne Volumenconstraint, Lastkurve, Snapshot-Roundtrip, Abwärtskompatibilität und automatisches Laden von Druckplot/Animation im selben Test geprüft. Der erste Sandbox-Versuch wurde vor Netzgenerierung durch fehlende Leserechte auf Gmsh blockiert; nur der anschließend freigegebene Versuch hat FEBio ausgeführt.

Aktuelle Auswertungserweiterung: Druckdiagramm unter der Kraftkurve, standardmäßig auf Indentation begrenzt, vollständiger Druckaufbau optional. Beide Zeitmarkierungen folgen derselben Animation; PNG/SVG enthält beide Diagramme. Automatische kraftbasierte Kontakterkennung mit drei aufeinanderfolgenden belasteten Punkten und relativer/absoluter Rauschschwelle, Kontaktweg als geklammerte lokale Nullkraftextrapolation, Regression aller belasteten Punkte bis zum erreichten Maximalweg. Keine manuellen Fit-Felder mehr. Fünf gezielte Prüfungen bestanden (vier Ergebnis-Unit-Tests, ein GUI-Test mit gespeicherten Daten einschließlich Altdatei-Wechsel); Ansicht bei 1680×1000 und 1120×780 geprüft. Keine neue Solverrechnung. Kontakterkennung bleibt eine aus diskreten Kraftdaten geschätzte Größe.

- FEBio beendet den Lauf erfolgreich und liefert einen Return-Code 0.
- Die Druckphase erreicht ein Gleichgewicht ohne Solver-Abbruch.
- Die Indentationsphase hält das nach Druckaufbau erreichte Innenvolumen konstant und erzeugt Kontakt; der Druck ist eine Ergebnisgröße.
- Die Reaktionskraft an der Messschalen-Befestigung ist für mehrere Lastschritte verfügbar.
- Der Lauf ist mit einem kleinen groben Netz reproduzierbar.

## Ursprünglicher Prototyp-Status (0,1 mm Spalt; unten korrigiert)

- Parametrischer 3°-Wedge für beide Schalen erzeugt; Baseline: 453 verwendete Knoten und 983 Tet4-Elemente.
- FEBio-4-XML mit Neo-Hookean, Innendruck, Symmetrieflächen, Kontakt, zwei Schritten und nodaler Reaktionskraft-Ausgabe erzeugt.
- XML-Struktur und positive Elementorientierung automatisch geprüft.
- **Solver-Smoke-Test bestanden (02.10.2026):** FEBio 4.13.0 akzeptiert die Eingabe, konvergiert über beide Phasen und terminiert normal mit Return-Code 0. Der Textexport enthält 22 Kurvenpunkte ab dem Ende der Druckphase bis 1 mm. Positive Kontaktdrücke sind im Log nachgewiesen.
- Kontaktvorlast nach der Druckphase: 0,02938 N; Kraft bei 1 mm: 0,14645 N (jeweils auf 360° skaliert). Der erneute Lauf bestätigt die Endkraft. Die Iterationswarnungen betreffen erfolgreich durchgeführte Steifigkeits-Neuberechnungen.
- Korrekturen aus dem echten Solverlauf: FEBio-4-Knotenlisten, einzelne DOF-Flags, `value` für vorgeschriebene Verschiebungen, expliziter Solid-Solver und adaptive Zeitschritte. Die Druckphase verwendet eine vorgeschriebene Nullverschiebung, damit FEBio die Halterungsreaktion ausgibt.
- **Ausstehend:** Netzkonvergenz, unabhängiger Drucktest, Prüfung von Symmetrieabweichungen und Dehnungen sowie wissenschaftliche Plausibilität der Kurve.
- Materialkarten A25/A30 enthalten unkalibrierte Prototypwerte. Vor einer Parameterstudie müssen Messdaten eingesetzt werden.

## Desktop-Ergebnisansicht (02.10.2026)

- Ergebnis-Dashboard in PySide6 mit eingebettetem Matplotlib-Plot, Hover-Werten, Zoom und PNG/SVG-Export.
- Fit-Arbeitsbereich direkt einstellbar; k, 1/k, R² und maximale Kraft werden aus der geladenen CSV berechnet. Die vorhandene Druckvorlast bleibt erhalten; keine erneute 360°-Skalierung.
- Axialschnitt aus den tatsächlichen Schnittflächen der zugehörigen `model.feb`, ohne Interpretation als verformtes Ergebnis.
- Numerische Regressionstests für Fit, konstante Kraft und CSV-Validierung; Qt-Start, Fit-Änderungen und PNG-Export geprüft. Darstellung bei 1280×900 und 1040×760 geprüft.
- GUI-Solverstart, Druckvergleich und Sweep sind weiterhin spätere Schritte.

## Netzanimation (02.10.2026)

- Fensterstart maximiert; Netzansicht nimmt den Hauptbereich ein, Fit/Kraftkurve stehen kompakt links.
- FEBio exportiert für alle Knoten ux/uy/uz über beide Phasen. Der Baseline-Lauf wurde erneut erfolgreich gerechnet und enthält 32 konvergierte Solverstände plus unbelastetes Referenznetz bei t=0.
- Export als `nodal_displacements.csv`, mit SHA-256-Bindung an `model.feb`; Reader prüft Zeitordnung, vollständige Knotenmengen und endliche Werte.
- Wiedergabe mit QTimer, Pause, Scrubbing und Tempo; Gesamtschnitt und Kontakt-Detail werden aus denselben Verschiebungen aufgebaut. Die Kamera bleibt über die Zeit fest; die Kurve zeigt synchron den aktuellen Indentationszustand.
- Darstellung 1× als Standard; 3×/5× optional und explizit gekennzeichnet. Radiale Spiegelung des Wedge-Schnitts, lineare Interpolation zwischen den gespeicherten Ständen.
- Geprüft: nichtuniforme Zeitinterpolation, Ablehnung falscher/inkompletter Daten, realer Endhub der Messhalterung von 1 mm, GUI-Wiedergabe/Pause/Zeitschieber, tatsächliche Änderung der Netzkoordinaten und synchrone Kurvenmarkierung.

## Korrektur: kontaktfreier Druckaufbau (02.10.2026)

- Der bisherige 0,1-mm-Spalt führte zu ungewollter Kontaktvorlast. Neuer zentraler Parameter `initial_gap_mm`, Baseline 1,0 mm; keine getrennten Abstandsdefaults mehr in Netzgenerator und FEBio-Writer.
- Neuer Lauf `work/simulations/baseline_gap1mm/`, alter Lauf unverändert erhalten. FEBio 4.13.0 terminiert normal; 24 Kurvenpunkte, 34 Animationsstände inklusive Referenz.
- Freie Bewegung der Druckschalenspitze bei 15 mmHg: −0,38494 mm in x; Restspalt 0,61506 mm. Kontaktflächen bleiben während aller gespeicherten Druckzustände axial getrennt, alle ausgegebenen Kontaktdrücke sind null. Halterungsreaktion bei t=1: rund 5e−14 N.
- Kontaktbeginn zwischen 0,60 und 0,65 mm Zustellweg, Endkraft bei 1 mm: 0,04086 N. Fit auf 0,75–1,0 mm verschoben, damit kein Leerweg in die Steifigkeitsauswertung eingeht. Die Achse bezeichnet ausdrücklich den gesamten Zustellweg, nicht die Eindrückung ab Erstkontakt.
- GUI bevorzugt den korrigierten Lauf; Anfangsspalt als Entwurfsfeld und gespeicherte Ergebnis-Metadaten sichtbar. Entwurfsänderungen starten weiterhin keinen Solverlauf.
- Neun Tests bestanden: unter anderem Gap-Validierung, Geometrie/Normalen mit abweichendem 1,3-mm-Testspalt sowie optionale Abnahme gegen die berechnete Baseline (Trennung über alle Druckzustände, vollständige Kontaktdruckprotokolle, keine Vorlast, Kontakt während Zustellung und Fit nach Erstkontakt).
- Für andere Parametersätze muss die kontaktfreie Druckphase erneut nachgewiesen werden; dieser Abstand ist nur für die aktuelle Baseline geprüft. Die wissenschaftlichen Validierungsaufgaben bleiben offen.

## GUI-Einzelruns und reduzierte Oberfläche (02.10.2026)

- Start im Modell-Tab, keine automatische Berechnung. Expliziter Startbutton, Solverauswahl und kurze Phasenanzeige. Kein paralleler Zweitstart; Eingaben während des Laufs gesperrt.
- `SimulationRunner` startet den CLI-Solver über QProcess. `--parameters` liest den vollständigen unveränderlichen JSON-Snapshot. Pro Lauf ein neuer Ordner, keine Überschreibung alter Ergebnisse; Gmsh bleibt im Hauptthread des separaten Prozesses.
- Erfolgreicher Exit allein genügt nicht: Status, Kraftkurve und zum Netz passende vollständige Verschiebungshistorie werden vor dem Erfolgssignal gelesen. Danach automatischer Ergebniswechsel. Fehlerdialog mit Details; bei Fehlern bleiben alte Ergebnisse erhalten. Schließen während eines aktiven Laufs wird verhindert.
- Live-Vorschau der unbelasteten Schalen mit tatsächlichen Radien/Wanddicken, Spaltmaß sowie Druck/Hub-Beschriftung. Entprellte Aktualisierung bei jeder Eingabeänderung.
- Nur noch Modell/Ergebnisse als Tabs; Header, Kennwertkarten und Animation ohne wiederholte Fußnoten. Technische Details in Tooltips; relevante Fehler, Kontaktvorlast und Fit-Warnungen bleiben sichtbar.
- 13 Tests bestanden, einschließlich zwei echten GUI-Rundläufen mit FEBio 4.13.0: (p=10 mmHg, R1=8 mm, Spalt=1 mm) und (p=20 mmHg, R1=9 mm, Spalt=1,2 mm). JSON-Parameter exakt verglichen, eigene Verzeichnisse, reagierende Qt-Ereignisschleife und automatisch geladene Animation bis t=2 nachgewiesen.
- Modell- und Ergebnisansicht bei 1680×1000, Modelleingabe zusätzlich bei 1120×780 visuell geprüft.

## Sklera-Startmodell (02.10.2026)

- Druckschale: auswählbares/voreingestelltes `sclera_demo`, FEBio `Mooney-Rivlin von Mises Fibers`; Messschale weiterhin Silikon. Parameter sind ausdrücklich unkalibriert, siehe README.
- Elementweise tangentiale `mat_axis` via FEBio-4-`MeshData/ElementData type="mat_axis"`; gleichmäßige In-Plane-Fasern (`kf=0`) ohne Symmetriebruch durch eine globale Vorzugsrichtung.
- Materialkarten-Snapshot in jedem neuen `case.json`; Ergebnisüberschrift unterscheidet Sklera von historischen Silikonläufen.
- Auf Nutzerwunsch genau ein gezielter End-to-End-Test, keine gesamte Testsuite: `FEM_RUN_SCLERA_TEST=1`, `python -m unittest discover -s tests -p test_sclera.py -v`.
- Bestanden mit FEBio 4.13.0: `run_20261002_234857_292a3062`, korrekte Materialzuordnung, orthonormale tangentiale Achsen aller Druckschalen-Elemente und automatisches Laden der Animation bis t=2. Reaktion nach Druckphase −1,06e−13 N, Endkraft 0,02857 N. Das ist ein Integrationsnachweis, keine Gewebevalidierung.

Die FEBio-Bausteine wurden gegen die offiziellen Abschnitte zu [Neo-Hookean](https://febiosoftware.github.io/febio-docs/features/features/solid_material_neo-hookean/), [Sliding Contact](https://febiosoftware.github.io/febio-docs/user/chapter3/3.14-contact-section/), [Drucklast](https://febiosoftware.github.io/febio-docs/features/features/solid_load_pressure/), [mehrstufigen Analysen](https://febiosoftware.github.io/febio-docs/user/chapter4/4.1-the-step-section/) und [Log-Ausgabe](https://febiosoftware.github.io/febio-docs/user/chapter3/3.19-output-section/) abgeglichen. Die kombinierte Eingabe wurde anschließend im installierten FEBio 4.13.0 ausgeführt.
