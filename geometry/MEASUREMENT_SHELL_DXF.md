# SolidWorks-DXF-Import für die Messschale

Die FEM-Software erwartet eine ASCII-DXF-Datei mit Linien und Kreisbögen in
Millimetern. SolidWorks kann eine Skizze als DXF exportieren und beim Import
wieder als 2D-Skizze einlesen.

## Layer

- `MS_MATERIAL_BOUNDARY_01`, `MS_MATERIAL_BOUNDARY_02`, ...: geschlossene Profilbereiche
- `MS_SENSOR_TIP`: genau ein Punkt oder Kreis für die Sensorspitze
- `MS_AXIS`: bestehende x-Achse bzw. Rotationsachse
- `MS_CONSTRUCTION`: sonstige Konstruktionsgeometrie

Jede nicht-Konstruktions-Komponente muss eine geschlossene Kontur bilden. Die
Materialzuweisung erfolgt über `measurement_shell_definition.example.json`.

## Python-Aufruf

```python
from geometry.measurement_shell_dxf import import_dxf_file

definition = import_dxf_file(
    "measurement_shell.dxf",
    "measurement_shell_definition.json",
)
```

Der Rückgabewert ist das versionierte interne JSON-Modell mit Punkten,
Linien, Bögen, Regionen und `metadata.sensor_tip_point`. Die FEM-Anbindung
kann anschließend ausschließlich dieses Modell verwenden; SolidWorks selbst
muss auf dem Simulationsrechner nicht installiert sein.
