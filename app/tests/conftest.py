"""Chemins des tests de l'application.

`src` pour `coeur`, et `src/engine` parce que les modules du moteur s'importent
à plat entre eux — exactement comme côté serveur.
"""

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
for chemin in (RACINE / "src", RACINE / "src" / "engine"):
    if str(chemin) not in sys.path:
        sys.path.insert(0, str(chemin))
