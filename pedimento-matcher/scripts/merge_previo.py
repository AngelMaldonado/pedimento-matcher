#!/usr/bin/env python3
"""Paso 2 — Merge acumulativo de paquetes de Previo (fotos_vN).

Si una partida (carpeta P<n>) aparece en mas de un paquete fotos_vN, gana el
paquete con N mas alto para ESA partida — reemplazo completo de la carpeta
ganadora, no fusion de archivos individuales dentro de la carpeta.

Uso:
    python3 merge_previo.py <directorio_de_trabajo>

Escribe <directorio>/previo/merged.json con el mapeo partida -> paquete ganador.
No copia ni mueve archivos: fotos_vN sigue siendo la fuente de verdad en disco,
el manifest es lo que el Paso 4 (analisis de fotos, no implementado todavia)
debe leer para saber que carpeta usar por partida.
"""
import json
import re
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        print("uso: merge_previo.py <directorio_de_trabajo>", file=sys.stderr)
        sys.exit(1)

    workdir = Path(sys.argv[1])
    previo_dir = workdir / "previo"
    if not previo_dir.is_dir():
        print(f"no existe {previo_dir}", file=sys.stderr)
        sys.exit(1)

    paquetes = sorted(
        (p for p in previo_dir.iterdir() if p.is_dir() and re.fullmatch(r"fotos_v(\d+)", p.name)),
        key=lambda p: int(re.fullmatch(r"fotos_v(\d+)", p.name).group(1)),
    )
    if not paquetes:
        print(f"no hay paquetes fotos_vN bajo {previo_dir}", file=sys.stderr)
        sys.exit(1)

    # Acumular: recorrer paquetes de mas viejo a mas nuevo, el mas nuevo
    # pisa la entrada de una partida ya vista (reemplazo completo de carpeta).
    partidas: dict[str, dict] = {}
    for paquete in paquetes:
        n = int(re.fullmatch(r"fotos_v(\d+)", paquete.name).group(1))
        for partida_dir in sorted(paquete.iterdir()):
            if not partida_dir.is_dir() or not re.fullmatch(r"P\d+", partida_dir.name):
                continue
            fotos = sorted(
                f.name for f in partida_dir.iterdir()
                if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")
            )
            partidas[partida_dir.name] = {
                "paquete": paquete.name,
                "paquete_version": n,
                "path": str(partida_dir.relative_to(workdir)),
                "fotos": fotos,
            }

    manifest = {
        "paquetes_considerados": [p.name for p in paquetes],
        "partidas": dict(
            sorted(partidas.items(), key=lambda kv: int(re.fullmatch(r"P(\d+)", kv[0]).group(1)))
        ),
    }

    out_path = previo_dir / "merged.json"
    out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"paquetes: {manifest['paquetes_considerados']}")
    print(f"partidas fusionadas: {len(partidas)}")
    print(f"manifest: {out_path.relative_to(workdir)}")


if __name__ == "__main__":
    main()
