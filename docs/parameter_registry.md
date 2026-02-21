# ParameterRegistry

Die `ParameterRegistry` ist die zentrale Quelle fuer alle Parameter, die die GUI anzeigt
und in Sweeps verwenden kann. Jeder Eintrag definiert Typ, Default, Gueltigkeit und
Template-Familien, ohne harte Kopplung an ATH-Templates.

## Wie die GUI daraus Felder generiert
1. Alle Parameter mit `scope` `both` oder `constraint-only` werden im Project-Dialog angezeigt.
2. Alle Parameter mit `scope` `both` oder `batch-only` werden im Batch-Dialog angezeigt.
3. `param_type` steuert das Widget:
   - `float`/`int` -> Number Input (mit `allowed_range` als Min/Max)
   - `bool` -> Checkbox
   - `enum` -> Dropdown (mit `choices`)
4. `template_families_supported` filtert die Parameter je nach ausgewaehlter Template-Familie.
5. `excludes_with` und `requires` steuern die Validierung (UI und CLI).
6. `ath_mapping` ist ein Platzhalter, wie spaeter Parameter in ATH-Configs gemappt werden.

## Erweiterung
- Legacy-Parameterdefinitionen liegen in `tools/legacy/parameter_registry.py` (non-shipping quarantine).
- Bestehende Parameter koennen ohne GUI-Aenderungen erweitert werden (Registry ist die Quelle).
