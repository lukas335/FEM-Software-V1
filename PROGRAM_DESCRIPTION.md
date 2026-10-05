# FEM-Software-V1 – Programmbeschreibung

## Zweck

FEM-Software-V1 ist eine Python-/PySide6-Anwendung zur Untersuchung einer rotationssymmetrischen Druck- und Eindrückmessung mit FEBio 4.x. Das Programm erzeugt ein 3°-3D-Wedge-Modell, startet FEBio, steuert entweder einen Zustellweg oder eine Zielkraft und wertet Kraft-, Druck- und Wegdaten aus.

Die Anwendung ist ein Forschungs-/Prototypwerkzeug. Materialkarten und Beispielmaterialien sind teilweise unkalibriert und dürfen nicht ohne Validierung als biologische Messwerte interpretiert werden.

## Technische Komponenten

- Python 3.13
- PySide6 für die grafische Oberfläche
- Gmsh-Python-API für die Tet4-Netzerzeugung
- FEBio 4.13.x als externer nichtlinearer FE-Solver
- NumPy für numerische Auswertung
- Matplotlib für Kurven und Ergebnisdarstellung
- YAML-Konfiguration unter `config/defaults.yaml`

Startpunkt ist `main.py`. Die Anwendung erwartet eine installierte Gmsh-Python-Bibliothek und einen auswählbaren `febio4.exe`.

## Benutzeroberflächen

### Einzelrechnung

Der Modell-Tab definiert:

- Messschale: Außenradius, Pol-/Randdicke und Material
- Druckkugel: Radius, Wanddicke und Material
- Anfangsdruck und Druckmodus
- Anfangsspalt
- Zustellsteuerung: Wegsteuerung oder Zielkraftregelung
- maximaler Zustellweg
- Wedge-Winkel und Netzqualität
- optionale Deckschale mit Dicken, zwei Materialien, freiem Kappenwinkel und Mooney-Rivlin-Koeffizienten

Nach der Rechnung können Kraft-Weg-Kurven, Druck-/Volumeninformationen und – bei Einzel-/Visualisierungsläufen – Mesh-Deformationen angezeigt werden.

### Parameterstudie

Eine Studie übernimmt ein vollständiges Basismodell und variiert Messschalenradius, Pol-/Randdicken und Druckwerte. Referenzgeometrien können zusätzlich gerechnet werden. Die Deckschalenparameter sind Bestandteil der Studienkonfiguration und werden nicht mehr nur implizit aus dem Modell-Tab übernommen.

Mehrere unabhängige Fälle können parallel laufen. Studienläufe verwenden standardmäßig den kompakten Datenmodus ohne vollständige Knotenhistorie und ohne XPLT-Datei.

## Physikalisches Modell

Das Modell verwendet die x-Achse als Rotationsachse und einen 3°-Wedge:

- offene Messschale auf der linken Seite
- hohle, nicht fest eingespannte Druckkugel
- optionale vollständige Deckschale um die Druckkugel
- Deckschale mit vorderem und hinterem Materialgebiet
- gemeinsame Knoten zwischen den Deckschalen-Materialgebieten
- reibungsfreier sliding-elastic Kontakt
- Druckkugel–Deckschale und Messschale–Deckschale als getrennte Kontaktkörper
- axiale/symmetrische Randbedingungen an den Wedge-Flächen
- Messschalen-Hinterkante als Aufspannungs-/Reaktionsbereich

Die Kontaktflächen der Druckkugel und Deckschale werden konform erzeugt und anschließend auf der Druckkugelseite geknotendupliziert. Dadurch werden anfängliche Kontaktüberdeckungen vermieden, ohne den Kontakt während der Rechnung künstlich geschlossen zu halten.

Materialkoeffizienten und physikalische Randbedingungen werden nicht durch numerische Optimierungen verändert.

## Geometrie und Netz

`geometry/mesh_generator.py` erzeugt ein Tet4-Netz. Die Geometrie wird über OCC/Gmsh aufgebaut, physikalische Volumina und Flächen werden benannt und anschließend in `WedgeMesh` überführt.

Wichtige Netz-/NodeSets:

- `measurement_support`: Knoten der Messschalen-Hinterkante/Aufspannung
- `measurement_inner_pole`: innerer Polknoten der Messschale bei `r=0`, geometrisch bei `x=-tx`, `y=z=0`
- Druck- und Deckschalen-Kontakt-/Symmetrieflächen

Die lokale Vergröberung des hinteren Deckschalenbereichs ist in `geometry/cover_mesh_sizing.py` definiert. Kontaktflächen werden von ungeeigneter Vergröberung ausgenommen.

## FEBio-Eingabe und numerische Lösung

`solver/febio_writer.py` schreibt die FEBio-Eingabe:

- Material- und Volumendefinitionen
- Kontaktpaare
- Drucklast und Lastkurven
- Randbedingungen und Symmetriebedingungen
- Druckaufbau und anschließende Zustellung
- Solver `solid`
- `ls_check_jacobians=1` als Schutz gegen invertierte Elemente während der Line Search

Die ursprüngliche Kontaktphysik bleibt reibungsfrei. Die numerische Sicherung gegen negative Jacobians ignoriert keine Elementumkehr; ein solcher Zustand führt weiterhin zu einem Fehler.

## Solver-Workflows

### Wegsteuerung

`solver/prototype.py` erzeugt das Modell, führt FEBio aus, liest Reaktionskräfte und erstellt `force_displacement.csv`.

### Zielkraftsteuerung

`solver/target_force.py` und `solver/febio_session.py` arbeiten mit isolierten Restart-Versuchen:

1. Druckaufbau bis zum Breakpoint
2. Erstellung eines geprüften Restart-Checkpoints
3. adaptives Vorschlagen eines nächsten Zustellschritts
4. FEBio-Restart in einem eigenen Trial-Verzeichnis
5. Akzeptieren oder Verwerfen des Zustands
6. Abbruch bei erreichter Zielkraft, Fehler oder Zustellgrenze

FEBio-Prozessfehler, Restartfehler und Auswertungsfehler werden getrennt behandelt. Ein Exitcode allein gilt nicht als Konvergenznachweis.

## Weg- und Kraftdefinition

Die Gesamt-Kraft wird aus den `Rx`-Reaktionen der `measurement_support`-Knoten gebildet und vom 3°-Wedge auf 360° skaliert:

\[
F_{360}=-\sum R_x\frac{360}{\alpha}
\]

Der relevante Eindrückweg ist nicht der absolute Weg der Aufspannung. Pro Zustand wird verwendet:

\[
u_\mathrm{rel}=\overline{u}_{x,\mathrm{Hinterkante}}-u_{x,\mathrm{innerer\ Pol}}
\]

Der innere Pol ist der Knoten `measurement_inner_pole`; die Hinterkante ist der Mittelwert der `measurement_support`-Knoten.

Nichtmonotone relative Wege werden nicht als Solverfehler gewertet. Die zeitliche Reihenfolge der Kurvenpunkte bleibt erhalten; Kontaktumlagerungen und lokale Rückläufe dürfen in den Ergebnisdaten vorkommen.

## Parameterstudienbewertung

Die Studienbewertung verwendet nicht mehr die Änderung einer regressierten Steifigkeit. Für jede Geometrie werden die Zustellwege bei erreichter Endkraft verglichen:

\[
\Delta u_\mathrm{Endkraft}=u_\mathrm{Endkraft}(P_\mathrm{hoch})-u_\mathrm{Endkraft}(P_\mathrm{niedrig})
\]

Bei mehr als zwei Druckwerten werden der niedrigste und höchste Druck verwendet. Der Wert wird in Millimetern angezeigt und für das Ranking verwendet.

Die bisherige Kraft-Weg-Regression bleibt als zusätzliche Einzelfallinformation verfügbar, ist aber nicht mehr die primäre Studienmetrik.

## Datenprodukte

Ein normaler Visualisierungslauf kann enthalten:

- `model.feb`
- `parameters.json`
- `case.json`
- `solver.log`, `stdout.txt`, `stderr.txt`, `launcher.log`
- `force_displacement.csv`
- `nodal_displacements.csv`
- `cavity_history.csv`
- `model.xplt`
- Restart- und Checkpoint-Dateien bei Zielkraftregelung

Ein kompakter Parameterstudienfall speichert keine vollständige Knotenhistorie und kein `model.xplt`. Er speichert nur die für Status, Kraft-/Wegbewertung und Diagnose erforderlichen Dateien.

## Fehlerdiagnose

Bei Fehlern bleiben die Solverdateien im jeweiligen Laufverzeichnis erhalten. `case.json` enthält – soweit verfügbar – eine kurze Fehlerursache und die vorhandenen Logdateien. Typische Kategorien sind:

- FEBio-Konvergenzfehler oder negative Jacobians
- fehlender/ungültiger Restart-Checkpoint
- unvollständige Reaktionsdaten
- ungültige Kraft-/Wegauswertung
- fehlende oder inkonsistente Studienparameter

Die vollständigen Logs werden nicht in die kurze GUI-Statuszeile kopiert; dort werden Ursache und Laufpfad angezeigt.

## Reproduzierbarkeit und Grenzen

Jeder Lauf besitzt ein eigenes Verzeichnis und eine unveränderliche Parametersnapshot-Datei. Fehlgeschlagene Restart-Zweige werden nicht als akzeptierte Zustände verwendet.

Die Anwendung ersetzt keine Netzkonvergenzstudie, Materialkalibrierung oder experimentelle Validierung. Änderungen an Materialdaten, Kontaktreibung, physikalischen Einspannungen oder Geometrie sind fachliche Modelländerungen und keine reinen numerischen Optimierungen.

## Entwicklung

Wichtige Bereiche:

- `app/`: GUI und Studiensteuerung
- `geometry/`: Geometrie, Netz und Knotensets
- `model/`: Parameter, Studienmodell und Ergebnismodelle
- `solver/`: FEBio-Schreiber, Prozesssteuerung, Restart- und Auswertungscode
- `visualization/`: Geometrie- und Ergebnisdarstellung
- `tests/`: gezielte Regressionstests

Große Laufdaten unter `work/` sowie virtuelle Umgebungen, Logs und Plotdateien sind durch `.gitignore` vom Repository ausgeschlossen.
